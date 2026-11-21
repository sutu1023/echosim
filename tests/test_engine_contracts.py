"""引擎领域契约：原创夹具、UTF-16 偏移与结构化结果校验。"""

from __future__ import annotations

import unittest

from app.engines.contracts import (
    AcceptanceMaterial,
    ItemResult,
    build_acceptance_material,
    build_action_content,
    build_graph_fact,
    build_target_post_content,
    utf16_len,
    utf16_slice,
    validate_structured_payload,
)


def valid_payload(material: AcceptanceMaterial) -> dict[str, object]:
    index = material.text.index(material.marker)
    start = utf16_len(material.text[:index])
    end = start + utf16_len(material.marker)
    return {
        "materialId": material.material_id,
        "summary": "社区图书角收到蓝色书箱。",
        "facts": [
            {
                "text": "蓝色书箱编号",
                "evidenceQuote": material.marker,
                "sourceStart": start,
                "sourceEnd": end,
                "kind": "ownership",
            }
        ],
    }


class MaterialFixtureTests(unittest.TestCase):
    def test_material_and_markers_are_unique_per_run(self) -> None:
        first = build_acceptance_material("run-a")
        second = build_acceptance_material("run-b")

        self.assertNotEqual(first.marker, second.marker)
        self.assertIn(first.marker, first.text)
        self.assertNotIn(second.marker, first.text)
        self.assertIn("河畔社区图书角", first.text)
        self.assertNotIn("private-inputs", first.text)

    def test_graph_and_action_fixtures_use_the_run_marker(self) -> None:
        material = build_acceptance_material("run-c")

        self.assertIn(material.marker, build_graph_fact(material))
        self.assertIn(material.run_id, build_action_content("platform_a", material))
        self.assertIn(material.run_id, build_action_content("platform_b", material))
        self.assertIn(material.run_id, build_target_post_content("platform_b", material))

    def test_utf16_offsets_handle_emoji(self) -> None:
        material = AcceptanceMaterial("r", "m", "MARK", "甲😀乙 MARK")
        start = utf16_len("甲😀乙 ")

        self.assertEqual(start, 5)
        self.assertEqual(utf16_slice(material.text, start, start + 4), "MARK")

    def test_utf16_slice_rejects_out_of_range(self) -> None:
        with self.assertRaises(ValueError):
            utf16_slice("abc", 0, 4)


class StructuredValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.material = build_acceptance_material("validate")

    def test_valid_payload_passes(self) -> None:
        result = validate_structured_payload(valid_payload(self.material), self.material)

        self.assertTrue(result.ok)
        self.assertEqual(result.source_matches, 1)
        self.assertTrue(result.marker_matched)

    def test_missing_field_fails(self) -> None:
        payload = valid_payload(self.material)
        payload["facts"][0].pop("kind")  # type: ignore[index]

        result = validate_structured_payload(payload, self.material)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "invalid_output")

    def test_wrong_type_fails(self) -> None:
        payload = valid_payload(self.material)
        payload["facts"][0]["sourceStart"] = "0"  # type: ignore[index]

        result = validate_structured_payload(payload, self.material)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "invalid_output")

    def test_source_slice_mismatch_fails(self) -> None:
        payload = valid_payload(self.material)
        payload["facts"][0]["evidenceQuote"] = "不存在的引用"  # type: ignore[index]

        result = validate_structured_payload(payload, self.material)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "source_mismatch")

    def test_missing_run_marker_fails(self) -> None:
        payload = valid_payload(self.material)
        payload["facts"][0]["evidenceQuote"] = "河畔社区图书角"  # type: ignore[index]
        payload["facts"][0]["sourceStart"] = 0  # type: ignore[index]
        payload["facts"][0]["sourceEnd"] = utf16_len("河畔社区图书角")  # type: ignore[index]

        result = validate_structured_payload(payload, self.material)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "marker_missing")

    def test_empty_facts_fails(self) -> None:
        payload = valid_payload(self.material)
        payload["facts"] = []

        result = validate_structured_payload(payload, self.material)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "invalid_output")

    def test_unknown_reason_cannot_be_passed(self) -> None:
        item = ItemResult("x", "model", "stage", "passed", "not-a-reason")

        self.assertEqual(item.status, "failed")


if __name__ == "__main__":
    unittest.main()
