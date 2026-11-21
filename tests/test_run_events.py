"""run 事件游标、字段与 SSE 帧的单元测试。"""

from __future__ import annotations

import json
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from app.errors import InvalidCursor
from app.services import run_events

RUN_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_RUN = uuid.UUID("22222222-2222-2222-2222-222222222222")
EPOCH = datetime(2026, 11, 21, 9, 0, 0, tzinfo=timezone.utc)


class CursorTests(unittest.TestCase):
    def test_missing_cursor_starts_at_zero(self) -> None:
        self.assertEqual(
            run_events.parse_cursor(None, None, run_id=RUN_ID, last_seq=4), 0
        )

    def test_after_equal_to_max_is_valid(self) -> None:
        self.assertEqual(
            run_events.parse_cursor(None, "4", run_id=RUN_ID, last_seq=4), 4
        )

    def test_after_beyond_max_is_invalid(self) -> None:
        with self.assertRaises(InvalidCursor):
            run_events.parse_cursor(None, "5", run_id=RUN_ID, last_seq=4)

    def test_last_event_id_wins_over_after(self) -> None:
        cursor = run_events.parse_cursor(
            f"{RUN_ID}:3", "1", run_id=RUN_ID, last_seq=4
        )
        self.assertEqual(cursor, 3)

    def test_last_event_id_for_other_run_is_invalid(self) -> None:
        with self.assertRaises(InvalidCursor):
            run_events.parse_cursor(
                f"{OTHER_RUN}:3", None, run_id=RUN_ID, last_seq=4
            )

    def test_malformed_cursors_are_invalid(self) -> None:
        for value in ("-1", "abc", "1.5", "", f"{RUN_ID}", f"{RUN_ID}:-2"):
            with self.subTest(value=value):
                with self.assertRaises(InvalidCursor):
                    run_events.parse_cursor(
                        None, value, run_id=RUN_ID, last_seq=4
                    )

    def test_negative_last_event_id_is_invalid(self) -> None:
        with self.assertRaises(InvalidCursor):
            run_events.parse_cursor(
                f"{RUN_ID}:-1", None, run_id=RUN_ID, last_seq=4
            )


class EventShapeTests(unittest.TestCase):
    def test_simulated_time_advances_one_minute_per_seq(self) -> None:
        self.assertEqual(
            run_events.simulated_time(EPOCH, 1), EPOCH
        )
        self.assertEqual(
            run_events.simulated_time(EPOCH, 4),
            EPOCH + timedelta(minutes=3),
        )

    def test_serialized_event_has_six_fields(self) -> None:
        event = run_events.RunEvent(
            run_id=RUN_ID,
            seq=4,
            type="progress",
            stage="dual-platform-run",
            simulated_at=run_events.simulated_time(EPOCH, 4),
            payload={"step": 4},
        )

        body = run_events.serialize(event)

        self.assertEqual(
            set(body), {"runId", "seq", "type", "stage", "simulatedAt", "payload"}
        )
        self.assertEqual(body["runId"], str(RUN_ID))
        self.assertEqual(body["simulatedAt"], "2026-11-21T09:03:00Z")

    def test_sse_frame_carries_run_scoped_id(self) -> None:
        event = run_events.RunEvent(
            run_id=RUN_ID,
            seq=4,
            type="progress",
            stage="dual-platform-run",
            simulated_at=EPOCH,
            payload={},
        )

        frame = run_events.sse_frame(event)

        self.assertTrue(frame.startswith(f"id: {RUN_ID}:4\n"))
        self.assertIn("event: run-event\n", frame)
        self.assertTrue(frame.endswith("\n\n"))
        data_line = next(
            line for line in frame.splitlines() if line.startswith("data: ")
        )
        payload = json.loads(data_line[len("data: ") :])
        self.assertEqual(payload["seq"], 4)

    def test_stream_end_frame_has_no_business_id(self) -> None:
        frame = run_events.stream_end_frame(RUN_ID, 10)

        self.assertTrue(frame.startswith("event: stream-end\n"))
        self.assertNotIn("id:", frame)
        self.assertIn('"lastSeq": 10', frame)

    def test_decodes_jsonb_text_payload(self) -> None:
        self.assertEqual(
            run_events._decode_payload('{"step": 3}'), {"step": 3}
        )
        self.assertEqual(run_events._decode_payload("not json"), {})
        self.assertEqual(run_events._decode_payload(None), {})


if __name__ == "__main__":
    unittest.main()
