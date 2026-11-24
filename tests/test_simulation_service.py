"""双平台运行接口与服务：开始去重、终态重跑、停止、边界与记账。"""

from __future__ import annotations

import asyncio
import unittest
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.engines.duet_config import DuetConfig
from app.errors import SelectionNotFound, SimulationConflict, SimulationNotFound
from app.services import simulation as store
from tests.db_support import (
    HAS_TEST_DATABASE,
    SKIP_REASON,
    apply_all_migrations,
    make_test_engine,
    reset_public_schema,
)
from tests.simulation_support import prepare_selection

CONFIG = DuetConfig()


@unittest.skipUnless(HAS_TEST_DATABASE, SKIP_REASON)
class SimulationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = make_test_engine()
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        await reset_public_schema(self.engine)
        await apply_all_migrations(self.engine)
        self.frozen = await prepare_selection(self.session_factory, marker="sim-service")
        self.project_id = self.frozen.seed.project_id
        self.actor_id = self.frozen.seed.user_id

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _start(self, **kwargs):
        async with self.session_factory() as db:
            return await store.start_simulation(
                db,
                project_id=self.project_id,
                actor_id=self.actor_id,
                selection_id=self.frozen.selection_id,
                engine_mode=kwargs.get("engine_mode"),
                allow_oasis=kwargs.get("allow_oasis", False),
                default_engine=kwargs.get("default_engine", "double"),
            )

    async def test_start_is_202_then_deduplicated(self) -> None:
        simulation, created = await self._start()
        self.assertTrue(created)
        self.assertEqual(simulation.engine_mode, "double")
        self.assertEqual(simulation.rounds, 3)
        self.assertEqual(simulation.question, self.frozen.seed.question)
        again, created_again = await self._start()
        self.assertFalse(created_again)
        self.assertEqual(again.id, simulation.id)
        async with self.session_factory() as db:
            rows = await db.execute(
                text("SELECT COUNT(*) FROM simulations WHERE project_id = :p"),
                {"p": self.project_id},
            )
            self.assertEqual(int(rows.scalar_one()), 1)
            jobs = await db.execute(
                text(
                    "SELECT COUNT(*) FROM jobs WHERE project_id = :p AND status = 'queued'"
                ),
                {"p": self.project_id},
            )
            self.assertEqual(int(jobs.scalar_one()), 1)

    async def test_terminal_rerun_creates_new_run(self) -> None:
        simulation, _ = await self._start()
        async with self.session_factory() as db:
            await db.execute(
                text(
                    "UPDATE simulations SET phase = 'succeeded', finished_at = now() "
                    "WHERE id = :id"
                ),
                {"id": simulation.id},
            )
            await db.execute(
                text("UPDATE jobs SET status = 'succeeded' WHERE run_id = :r"),
                {"r": simulation.run_id},
            )
            await db.commit()
        second, created = await self._start()
        self.assertTrue(created)
        self.assertNotEqual(second.run_id, simulation.run_id)
        async with self.session_factory() as db:
            old = await store.get_simulation(
                db, project_id=self.project_id, run_id=simulation.run_id
            )
        self.assertTrue(old.terminal)
        self.assertEqual(old.phase, "succeeded")

    async def test_missing_and_foreign_selection_rejected(self) -> None:
        with self.assertRaises(SelectionNotFound):
            async with self.session_factory() as db:
                await store.start_simulation(
                    db,
                    project_id=self.project_id,
                    actor_id=self.actor_id,
                    selection_id=uuid.uuid4(),
                    engine_mode=None,
                    allow_oasis=False,
                    default_engine="double",
                )
        async with self.session_factory() as db:
            count = await db.execute(
                text("SELECT COUNT(*) FROM simulations WHERE project_id = :p"),
                {"p": self.project_id},
            )
            self.assertEqual(int(count.scalar_one()), 0)

    async def test_oasis_requires_explicit_enable(self) -> None:
        with self.assertRaises(SimulationConflict) as caught:
            await self._start(engine_mode="oasis", allow_oasis=False)
        self.assertEqual(caught.exception.code, "engine_not_enabled")
        simulation, created = await self._start(
            engine_mode="oasis", allow_oasis=True, default_engine="double"
        )
        self.assertTrue(created)
        self.assertEqual(simulation.engine_mode, "oasis")

    async def test_cancel_for_run_freezes_run(self) -> None:
        simulation, _ = await self._start()
        async with self.session_factory() as db:
            await store.cancel_for_run(db, run_id=simulation.run_id)
            await db.execute(
                text(
                    "UPDATE jobs SET status = 'canceled', finished_at = now() "
                    "WHERE run_id = :r"
                ),
                {"r": simulation.run_id},
            )
            await db.commit()
        async with self.session_factory() as db:
            refreshed = await store.get_simulation(
                db, project_id=self.project_id, run_id=simulation.run_id
            )
        self.assertEqual(refreshed.phase, "canceled")
        self.assertFalse(refreshed.terminal is False)

    async def test_invalid_platform_and_engine_mode_are_rejected(self) -> None:
        simulation, _ = await self._start()
        async with self.session_factory() as db:
            for sql, params in (
                (
                    "UPDATE simulations SET engine_mode = 'bogus' WHERE id = :id",
                    {"id": simulation.id},
                ),
                (
                    "UPDATE simulation_platforms SET native_platform = 'reddit' "
                    "WHERE simulation_id = :id AND platform_key = 'platform_a'",
                    {"id": simulation.id},
                ),
            ):
                with self.subTest(sql=sql.split()[1]):
                    with self.assertRaises(Exception):
                        await db.execute(text(sql), params)
                    await db.rollback()

    async def test_unknown_run_not_found(self) -> None:
        with self.assertRaises(SimulationNotFound):
            async with self.session_factory() as db:
                await store.get_simulation(
                    db, project_id=self.project_id, run_id=uuid.uuid4()
                )


class SimulationBudgetAndTerminalTests(SimulationServiceTests):
    async def _run_row(self, simulation):
        async with self.session_factory() as db:
            return (
                await db.execute(
                    text(
                        "SELECT stage, simulated_epoch FROM runs WHERE id = :r"
                    ),
                    {"r": simulation.run_id},
                )
            ).first()

    async def _terminate(self, simulation, status: str):
        row = await self._run_row(simulation)
        async with self.session_factory() as db:
            current = await store.get_simulation(
                db, project_id=self.project_id, run_id=simulation.run_id
            )
            applied, final = await store.terminate(
                db,
                simulation=current,
                status=status,
                reason=status,
                failed_platforms=[],
                stage=row.stage,
                simulated_epoch=row.simulated_epoch,
            )
            await db.commit()
        return applied, final

    async def _terminal_events(self, simulation) -> list[str]:
        async with self.session_factory() as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT type FROM run_events WHERE run_id = :r "
                        "AND type IN ('completed', 'failed', 'canceled') "
                        "ORDER BY seq"
                    ),
                    {"r": simulation.run_id},
                )
            ).fetchall()
        return [row.type for row in rows]

    async def _free_slot(self, simulation) -> None:
        async with self.session_factory() as db:
            await db.execute(
                text("UPDATE jobs SET status = 'succeeded' WHERE id = :id"),
                {"id": simulation.job_id},
            )
            await db.commit()

    async def test_reserve_and_release_share_one_budget(self) -> None:
        simulation, _ = await self._start()
        async with self.session_factory() as db:
            self.assertTrue(
                await store.reserve_actions(
                    db, simulation_id=simulation.id, count=4, max_actions=5
                )
            )
            await db.commit()
        async with self.session_factory() as db:
            self.assertFalse(
                await store.reserve_actions(
                    db, simulation_id=simulation.id, count=2, max_actions=5
                )
            )
            await db.commit()
        async with self.session_factory() as db:
            await store.release_actions(
                db, simulation_id=simulation.id, count=4
            )
            await db.commit()
        async with self.session_factory() as db:
            self.assertTrue(
                await store.reserve_actions(
                    db, simulation_id=simulation.id, count=5, max_actions=5
                )
            )
            await db.commit()

    async def test_terminal_race_keeps_one_winner_in_both_orders(self) -> None:
        first, _ = await self._start()
        self.assertEqual(await self._terminate(first, "succeeded"), (True, "succeeded"))
        self.assertEqual(
            await self._terminate(first, "canceled"), (False, "succeeded")
        )
        self.assertEqual(await self._terminal_events(first), ["completed"])
        await self._free_slot(first)

        second, _ = await self._start()
        self.assertEqual(await self._terminate(second, "canceled"), (True, "canceled"))
        self.assertEqual(
            await self._terminate(second, "succeeded"), (False, "canceled")
        )
        self.assertEqual(await self._terminal_events(second), ["canceled"])
        await self._free_slot(second)

        third, _ = await self._start()
        results = await asyncio.gather(
            self._terminate(third, "succeeded"),
            self._terminate(third, "canceled"),
            return_exceptions=True,
        )
        applied = [
            item for item in results if isinstance(item, tuple) and item[0]
        ]
        self.assertEqual(len(applied), 1, f"并发终态应只有一个赢家：{results}")
        self.assertEqual(len(await self._terminal_events(third)), 1)
        async with self.session_factory() as db:
            phase = (
                await db.execute(
                    text("SELECT phase FROM simulations WHERE run_id = :r"),
                    {"r": third.run_id},
                )
            ).scalar_one()
        self.assertEqual(str(phase), applied[0][1])
