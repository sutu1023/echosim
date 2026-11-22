"""业务建图配置：provider 解析、占位值与替身开关。"""

from __future__ import annotations

import unittest

from app.config import Settings
from app.services import evidence_graph as store
from app.engines.business_config import (
    BusinessGraphConfig,
    BusinessGraphConfigError,
    DEFAULT_EVIDENCE_GRAPH_INDEX_TIMEOUT_SECONDS,
    DEFAULT_EVIDENCE_GRAPH_MAX_DOCUMENTS,
    DEFAULT_EVIDENCE_GRAPH_MAX_EDGES,
    DEFAULT_EVIDENCE_GRAPH_MAX_NODES,
    DEFAULT_EVIDENCE_GRAPH_MAX_SOURCES_PER_ITEM,
    DEFAULT_EVIDENCE_GRAPH_PROCESSING_TIMEOUT_SECONDS,
    graph_id_fingerprint,
)


class ProviderResolutionTests(unittest.TestCase):
    def test_placeholders_resolve_to_unconfigured(self) -> None:
        config = BusinessGraphConfig.from_env(
            {
                "MODEL_API_BASE_URL": "https://model.example.invalid/v1",
                "MODEL_API_KEY": "replace-with-model-key",
                "MODEL_NAME": "replace-with-model-name",
                "ZEP_API_KEY": "replace-with-zep-key",
            }
        )

        selection = config.resolve_providers()

        self.assertEqual(selection.model, "unconfigured")
        self.assertEqual(selection.graph, "unconfigured")
        self.assertFalse(selection.model_configured)
        self.assertFalse(selection.graph_configured)

    def test_auto_uses_real_providers_only_when_configured(self) -> None:
        config = BusinessGraphConfig.from_env(
            {
                "MODEL_API_BASE_URL": "https://model.test.local/v1",
                "MODEL_API_KEY": "test-model-key",
                "MODEL_NAME": "test-model",
                "ZEP_API_KEY": "test-zep-key",
            }
        )

        selection = config.resolve_providers()

        self.assertEqual(selection.model, "http")
        self.assertEqual(selection.graph, "zep")
        self.assertTrue(selection.real_model)
        self.assertTrue(selection.real_graph)

    def test_graph_binding_does_not_require_acceptance_graph_id(self) -> None:
        config = BusinessGraphConfig.from_env(
            {
                "ZEP_API_KEY": "test-zep-key",
                "ZEP_ACCEPTANCE_GRAPH_ID": "",
            }
        )

        selection = config.resolve_providers()

        self.assertEqual(selection.graph, "zep")
        self.assertTrue(selection.graph_configured)

    def test_explicit_stub_without_allow_is_rejected(self) -> None:
        for key in (
            "EVIDENCE_GRAPH_MODEL_PROVIDER",
            "EVIDENCE_GRAPH_GRAPH_PROVIDER",
        ):
            with self.subTest(key=key):
                config = BusinessGraphConfig.from_env({key: "stub"})
                with self.assertRaises(BusinessGraphConfigError) as caught:
                    config.resolve_providers()
                self.assertEqual(caught.exception.code, "stub_not_allowed")

    def test_explicit_stub_with_allow_is_enabled(self) -> None:
        config = BusinessGraphConfig.from_env(
            {
                "EVIDENCE_GRAPH_MODEL_PROVIDER": "stub",
                "EVIDENCE_GRAPH_GRAPH_PROVIDER": "stub",
                "EVIDENCE_GRAPH_ALLOW_STUB": "true",
            }
        )

        selection = config.resolve_providers()

        self.assertEqual(selection.model, "stub")
        self.assertEqual(selection.graph, "stub")
        self.assertTrue(selection.stub_allowed)
        self.assertTrue(selection.uses_stub)

    def test_auto_never_falls_back_to_stub(self) -> None:
        config = BusinessGraphConfig.from_env(
            {"EVIDENCE_GRAPH_ALLOW_STUB": "true"}
        )

        selection = config.resolve_providers()

        self.assertEqual(selection.model, "unconfigured")
        self.assertEqual(selection.graph, "unconfigured")
        self.assertFalse(selection.uses_stub)

    def test_explicit_real_provider_without_credentials_is_unconfigured(self) -> None:
        config = BusinessGraphConfig.from_env(
            {
                "EVIDENCE_GRAPH_MODEL_PROVIDER": "http",
                "EVIDENCE_GRAPH_GRAPH_PROVIDER": "zep",
            }
        )

        selection = config.resolve_providers()

        self.assertEqual(selection.model, "unconfigured")
        self.assertEqual(selection.graph, "unconfigured")

    def test_unknown_provider_is_rejected(self) -> None:
        config = BusinessGraphConfig.from_env(
            {"EVIDENCE_GRAPH_MODEL_PROVIDER": "unknown"}
        )

        with self.assertRaises(BusinessGraphConfigError) as caught:
            config.resolve_providers()
        self.assertEqual(caught.exception.code, "invalid_provider")

    def test_non_finite_budgets_fall_back_to_positive_defaults(self) -> None:
        config = BusinessGraphConfig.from_env(
            {
                "EVIDENCE_GRAPH_INDEX_TIMEOUT_SECONDS": "inf",
                "EVIDENCE_GRAPH_PROCESSING_TIMEOUT_SECONDS": "nan",
            }
        )

        self.assertEqual(
            config.index_timeout_seconds,
            DEFAULT_EVIDENCE_GRAPH_INDEX_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            config.processing_timeout_seconds,
            DEFAULT_EVIDENCE_GRAPH_PROCESSING_TIMEOUT_SECONDS,
        )

    def test_hard_caps_cannot_be_raised_by_configuration(self) -> None:
        config = BusinessGraphConfig.from_env(
            {
                "EVIDENCE_GRAPH_MAX_DOCUMENTS": "99",
                "EVIDENCE_GRAPH_MAX_NODES": "999",
                "EVIDENCE_GRAPH_MAX_EDGES": "999",
                "EVIDENCE_GRAPH_MAX_SOURCES_PER_ITEM": "99",
            }
        )

        self.assertEqual(config.max_documents, DEFAULT_EVIDENCE_GRAPH_MAX_DOCUMENTS)
        self.assertEqual(config.max_nodes, DEFAULT_EVIDENCE_GRAPH_MAX_NODES)
        self.assertEqual(config.max_edges, DEFAULT_EVIDENCE_GRAPH_MAX_EDGES)
        self.assertEqual(
            config.max_sources_per_item,
            DEFAULT_EVIDENCE_GRAPH_MAX_SOURCES_PER_ITEM,
        )

    def test_app_settings_clamp_hard_caps_and_non_finite_budgets(self) -> None:
        settings = Settings(
            evidence_graph_max_nodes=999,
            evidence_graph_max_edges=999,
            evidence_graph_max_documents=99,
            evidence_graph_max_sources_per_item=99,
            evidence_graph_index_timeout_seconds=float("inf"),
            evidence_graph_processing_timeout_seconds=float("nan"),
        )

        self.assertEqual(settings.evidence_graph_max_nodes, 200)
        self.assertEqual(settings.evidence_graph_max_edges, 400)
        self.assertEqual(settings.evidence_graph_max_documents, 5)
        self.assertEqual(settings.evidence_graph_max_sources_per_item, 5)
        self.assertEqual(
            settings.evidence_graph_index_timeout_seconds,
            DEFAULT_EVIDENCE_GRAPH_INDEX_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            settings.evidence_graph_processing_timeout_seconds,
            DEFAULT_EVIDENCE_GRAPH_PROCESSING_TIMEOUT_SECONDS,
        )

    def test_acceptance_graph_fingerprint_is_derived_and_raw_id_not_kept(self) -> None:
        acceptance = "11111111-1111-4111-8111-111111111111"
        config = BusinessGraphConfig.from_env(
            {"ZEP_ACCEPTANCE_GRAPH_ID": acceptance}
        )

        self.assertEqual(
            config.acceptance_graph_fingerprint,
            graph_id_fingerprint(acceptance),
        )
        self.assertNotIn(
            acceptance, str(config.public_dict()) + str(config)
        )

    def test_placeholder_acceptance_graph_has_no_fingerprint(self) -> None:
        config = BusinessGraphConfig.from_env(
            {"ZEP_ACCEPTANCE_GRAPH_ID": "00000000-0000-0000-0000-000000000000"}
        )

        self.assertIsNone(config.acceptance_graph_fingerprint)

    def test_service_hard_caps_match_business_config(self) -> None:
        self.assertEqual(store.MAX_GRAPH_NODES, DEFAULT_EVIDENCE_GRAPH_MAX_NODES)
        self.assertEqual(store.MAX_GRAPH_EDGES, DEFAULT_EVIDENCE_GRAPH_MAX_EDGES)
        self.assertEqual(
            store.MAX_EVIDENCE_PER_TARGET,
            DEFAULT_EVIDENCE_GRAPH_MAX_SOURCES_PER_ITEM,
        )

    def test_public_dict_contains_no_secret_values(self) -> None:
        config = BusinessGraphConfig.from_env(
            {
                "MODEL_API_KEY": "super-secret-model-key",
                "ZEP_API_KEY": "super-secret-zep-key",
            }
        )

        payload = config.public_dict()

        self.assertNotIn("super-secret", str(payload))
        self.assertIn("modelProvider", payload)
        self.assertIn("graphProvider", payload)


if __name__ == "__main__":
    unittest.main()
