"""worker 租约守卫：令牌、状态与数据库时间的判定。"""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services import worker


def build_claim() -> worker.Claim:
    return worker.Claim(
        job_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        attempt=1,
        lease_token=uuid.uuid4(),
        next_step=0,
        total_steps=10,
        stage="dual-platform-run",
        fail_at_step=None,
        simulated_epoch=None,  # type: ignore[arg-type]
    )


NOW = datetime(2026, 11, 21, 9, 0, tzinfo=timezone.utc)


def lease_row(claim: worker.Claim, *, expires_at: datetime | None) -> SimpleNamespace:
    return SimpleNamespace(
        status="running",
        lease_token=claim.lease_token,
        lease_expires_at=expires_at,
    )


class LeaseGuardTests(unittest.TestCase):
    def test_matching_token_and_live_lease_passes(self) -> None:
        claim = build_claim()
        row = lease_row(claim, expires_at=NOW + timedelta(seconds=3))

        self.assertTrue(worker._lease_valid(row, claim, NOW))

    def test_old_token_is_rejected(self) -> None:
        claim = build_claim()
        row = SimpleNamespace(
            status="running",
            lease_token=uuid.uuid4(),
            lease_expires_at=NOW + timedelta(seconds=3),
        )

        self.assertFalse(worker._lease_valid(row, claim, NOW))

    def test_lease_expired_before_clock_read_is_rejected(self) -> None:
        """事务开始于到期前，但持锁后读到的时间已过到期点。"""
        claim = build_claim()
        row = lease_row(claim, expires_at=NOW - timedelta(milliseconds=200))

        self.assertFalse(worker._lease_valid(row, claim, NOW))

    def test_lease_without_expiry_is_rejected(self) -> None:
        claim = build_claim()
        row = lease_row(claim, expires_at=None)

        self.assertFalse(worker._lease_valid(row, claim, NOW))

    def test_terminal_state_is_rejected(self) -> None:
        claim = build_claim()
        for status in ("succeeded", "failed", "canceled"):
            with self.subTest(status=status):
                row = SimpleNamespace(
                    status=status,
                    lease_token=claim.lease_token,
                    lease_expires_at=NOW + timedelta(seconds=3),
                )
                self.assertFalse(worker._lease_valid(row, claim, NOW))


class StepOutcomeTests(unittest.TestCase):
    def test_outcome_carries_next_step(self) -> None:
        outcome = worker.StepOutcome("progress", 4)
        self.assertEqual(outcome.kind, "progress")
        self.assertEqual(outcome.next_step, 4)

    def test_claim_maps_row_values(self) -> None:
        token = uuid.uuid4()
        row = SimpleNamespace(
            id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            attempts=2,
            lease_token=token,
            next_step=3,
            total_steps=10,
            stage="report",
            fail_at_step=3,
            simulated_epoch=None,
        )

        claim = worker._as_claim(row)

        self.assertEqual(claim.lease_token, token)
        self.assertEqual(claim.attempt, 2)
        self.assertEqual(claim.next_step, 3)
        self.assertEqual(claim.fail_at_step, 3)


if __name__ == "__main__":
    unittest.main()
