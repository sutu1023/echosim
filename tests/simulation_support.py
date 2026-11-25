"""Change 09 运行测试夹具：真实准备 + 冻结选择 + 双平台运行输入。"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.engines.environment_config import EnvironmentConfig
from app.engines.environment_model import create_profiler
from app.services import environment as environment_store
from app.services import worker as worker_service
from tests.environment_support import SeededGraph, seed_rich_graph

CONFIG = EnvironmentConfig(
    model_provider="stub",
    allow_stub=True,
    profile_batch_size=2,
    profile_delay_ms=0,
)

ROUNDS = 3


@dataclass(frozen=True)
class SeededSimulation:
    seed: SeededGraph
    preparation_id: uuid.UUID
    selection_id: uuid.UUID
    payload: dict[str, Any]


async def prepare_selection(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    marker: str,
    rounds: int = ROUNDS,
    first_speaker_hours: Sequence[int] | None = None,
) -> SeededSimulation:
    seed = await seed_rich_graph(session_factory, marker=marker)
    async with session_factory() as db:
        preparation, created = await environment_store.start_preparation(
            db,
            project_id=seed.project_id,
            actor_id=seed.user_id,
            build_id=seed.build_id,
            config=CONFIG,
            retry=False,
        )
    assert created
    async with session_factory() as db:
        claim = await worker_service.claim_next(
            db,
            worker_id="simulation-test",
            lease_seconds=120,
            job_id=preparation.job_id,
        )
    assert claim is not None
    outcome = await worker_service.execute_preparation(
        claim,
        session_factory=session_factory,
        stop_event=None,
        lease_seconds=120,
        heartbeat_seconds=30,
        config=CONFIG,
        profiler_factory=lambda metrics=None: create_profiler(CONFIG, metrics=metrics),
    )
    assert outcome == "succeeded", outcome
    if first_speaker_hours:
        async with session_factory() as db:
            role_id = (
                await db.execute(
                    text(
                        "SELECT role_id FROM environment_activation WHERE "
                        "preparation_id = :prep AND is_first_speaker "
                        "ORDER BY position LIMIT 1"
                    ),
                    {"prep": preparation.id},
                )
            ).scalar_one()
            await db.execute(
                text(
                    "UPDATE environment_roles SET simulation = jsonb_set("
                    "simulation, '{activeHours}', CAST(:hours AS jsonb)) "
                    "WHERE role_id = :role_id"
                ),
                {
                    "hours": json.dumps(list(first_speaker_hours)),
                    "role_id": role_id,
                },
            )
            await db.commit()
    async with session_factory() as db:
        current = await environment_store.get_preparation(
            db, project_id=seed.project_id, preparation_id=preparation.id
        )
        await environment_store.update_configuration(
            db,
            project_id=seed.project_id,
            preparation_id=preparation.id,
            settings_revision=current.settings_revision,
            duration_hours=rounds,
            minutes_per_round=60,
            rounds_mode="manual",
            manual_rounds=rounds,
            config=CONFIG,
        )
        current = await environment_store.get_preparation(
            db, project_id=seed.project_id, preparation_id=preparation.id
        )
        _preparation, frozen = await environment_store.confirm_preparation(
            db,
            project_id=seed.project_id,
            preparation_id=preparation.id,
            settings_revision=current.settings_revision,
        )
    return SeededSimulation(
        seed=seed,
        preparation_id=preparation.id,
        selection_id=uuid.UUID(frozen["selectionId"]),
        payload=frozen,
    )


async def expire_lease(
    session_factory: async_sessionmaker[AsyncSession], *, job_id: uuid.UUID
) -> None:
    async with session_factory() as db:
        await db.execute(
            text(
                "UPDATE jobs SET lease_expires_at = clock_timestamp() - interval '5 seconds' "
                "WHERE id = :job_id"
            ),
            {"job_id": job_id},
        )
        await db.commit()
