"""任务服务纯逻辑：详情序列化、标识解析与常量契约。"""

from __future__ import annotations

import dataclasses
import unittest
import uuid
from datetime import datetime, timezone

from app.errors import JobNotFound
from app.services import jobs

NOW = datetime(2026, 11, 21, 9, 0, tzinfo=timezone.utc)


def build_job() -> jobs.Job:
    return jobs.Job(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        status="running",
        next_step=3,
        attempts=2,
        total_steps=jobs.TEST_TOTAL_STEPS,
        stage="dual-platform-run",
        lease_expires_at=NOW,
        terminal_reason=None,
        created_at=NOW,
        started_at=NOW,
        finished_at=None,
        cancel_requested_at=None,
        awaiting_takeover=False,
        lease_expired_seconds=0.0,
    )


class JobBodyTests(unittest.TestCase):
    def test_body_exposes_ids_and_progress(self) -> None:
        job = build_job()

        body = jobs.job_body(job, deduplicated=False)

        self.assertEqual(body["jobId"], str(job.id))
        self.assertEqual(body["runId"], str(job.run_id))
        self.assertEqual(body["stepsApplied"], 3)
        self.assertEqual(body["totalSteps"], jobs.TEST_TOTAL_STEPS)
        self.assertEqual(body["attempts"], 2)
        self.assertIs(body["deduplicated"], False)
        self.assertIs(body["awaitingTakeover"], False)

    def test_body_omits_deduplicated_when_not_starting(self) -> None:
        body = jobs.job_body(build_job())

        self.assertNotIn("deduplicated", body)

    def test_terminal_state_is_reported(self) -> None:
        job = dataclasses.replace(build_job(), status="canceled")
        self.assertTrue(job.terminal)


class JobIdentifierTests(unittest.TestCase):
    def test_invalid_job_id_is_job_not_found(self) -> None:
        for value in ("not-a-uuid", "", "123"):
            with self.subTest(value=value):
                with self.assertRaises(JobNotFound):
                    jobs._parse_job_id(value)

    def test_valid_job_id_round_trips(self) -> None:
        value = uuid.uuid4()
        self.assertEqual(jobs._parse_job_id(str(value)), value)


class TestTaskContractTests(unittest.TestCase):
    def test_deterministic_task_has_ten_steps(self) -> None:
        self.assertEqual(jobs.TEST_TOTAL_STEPS, 10)
        self.assertEqual(jobs.TEST_KIND, "test.simulation")
        self.assertEqual(jobs.ACTIVE_STATUSES, ("queued", "running"))
        self.assertEqual(
            jobs.TERMINAL_STATUSES, ("succeeded", "failed", "canceled")
        )


if __name__ == "__main__":
    unittest.main()
