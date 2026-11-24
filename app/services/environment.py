"""角色与环境准备持久化服务：绑定、开始/重试、详情、轮数与冻结选择。

所有读取都限定项目归属；准备只引用同一项目 build 的已发布最终快照。材料事实与
模拟设定分开保存，冻结选择是 Change 09 的唯一读取入口。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..engines.environment_contracts import (
    EnvironmentContractError,
    RoundSettings,
    compute_rounds,
    rounds_from_db,
    validate_simulation,
)
from ..engines.environment_roles import RoleDraft, RoleSelection, select_roles
from ..errors import (
    EnvironmentBuildNotFound,
    EnvironmentConflict,
    EnvironmentPreparationNotFound,
    ServiceUnavailable,
)
from . import evidence_graph as graph_store

logger = logging.getLogger(__name__)

DB_ERRORS = (SQLAlchemyError, OSError, asyncio.TimeoutError)

PREPARE_KIND = "agent.environment.prepare"
PREPARE_STAGE = "roles-environment"
PREPARE_TOTAL_STEPS = 5
PREPARE_STEP_KEYS = ("bind", "select", "profile", "compose", "publish")
PREPARE_OPERATION_KEY_PREFIX = "agent-environment-prepare:v1:"

ACTIVE_PHASES = ("preparing", "ready")
RETRYABLE_PHASES = ("failed", "canceled")
PLATFORM_MAPPING = (("platform_a", "twitter"), ("platform_b", "reddit"))

PREP_COLUMNS = (
    "p.id, p.project_id, p.build_id, p.snapshot_id, p.run_id, p.question, "
    "p.phase, p.duration_hours, p.minutes_per_round, p.rounds_mode, "
    "p.manual_rounds, p.rounds, p.settings_revision, "
    "p.confirmed_settings_revision, p.preview_role_count, "
    "p.profile_batch_count, p.provider_calls_real, p.provider_calls_double, "
    "p.failure_code, p.failure_message, p.profile_topics, "
    "p.created_at, p.updated_at, p.finished_at, j.id AS job_id, "
    "j.status AS job_status, "
    "EXISTS (SELECT 1 FROM environment_frozen_selections f "
    "        WHERE f.preparation_id = p.id) AS frozen"
)
PREP_SOURCE = (
    "FROM environment_preparations p JOIN jobs j ON j.run_id = p.run_id"
)


@dataclass(frozen=True)
class EnvironmentPreparation:
    id: uuid.UUID
    project_id: uuid.UUID
    build_id: uuid.UUID
    snapshot_id: uuid.UUID
    run_id: uuid.UUID
    question: str
    phase: str
    duration_hours: int
    minutes_per_round: int
    rounds_mode: str
    manual_rounds: int | None
    rounds: int
    settings_revision: int
    confirmed_settings_revision: int | None
    preview_role_count: int
    profile_batch_count: int
    provider_calls_real: int
    provider_calls_double: int
    failure_code: str | None
    failure_message: str | None
    profile_topics: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    job_id: uuid.UUID
    job_status: str
    frozen: bool

    @property
    def settings(self) -> RoundSettings:
        return rounds_from_db(
            duration_hours=self.duration_hours,
            minutes_per_round=self.minutes_per_round,
            rounds_mode=self.rounds_mode,
            manual_rounds=self.manual_rounds,
            rounds=self.rounds,
        )


@dataclass(frozen=True)
class EnvironmentRoleRow:
    role_id: uuid.UUID
    preparation_id: uuid.UUID
    snapshot_id: uuid.UUID
    node_key: str
    display_name: str
    role_type: str
    batch_index: int
    mention: dict[str, Any]
    background: dict[str, Any]
    expressed_topics: list[dict[str, Any]]
    involved_topics: list[dict[str, Any]]
    simulation: dict[str, Any] | None
    preview_ready_at: datetime | None
    published_at: datetime | None


@dataclass(frozen=True)
class EnvironmentTopic:
    topic_id: str
    topic_index: int
    text: str
    origin: str
    claim_nature: str | None
    claim_nature_known: bool
    sources: list[dict[str, Any]]


@dataclass(frozen=True)
class EnvironmentActivation:
    position: int
    role_id: uuid.UUID
    is_first_speaker: bool
    first_topic_id: str | None


@dataclass(frozen=True)
class EnvironmentContext:
    build: graph_store.GraphBuild
    snapshot: graph_store.GraphSnapshot | None
    can_prepare: bool
    reason: str | None
    preparation: EnvironmentPreparation | None
    newer_build_id: uuid.UUID | None


def _as_prep(row: object) -> EnvironmentPreparation:
    return EnvironmentPreparation(
        id=row.id,  # type: ignore[attr-defined]
        project_id=row.project_id,  # type: ignore[attr-defined]
        build_id=row.build_id,  # type: ignore[attr-defined]
        snapshot_id=row.snapshot_id,  # type: ignore[attr-defined]
        run_id=row.run_id,  # type: ignore[attr-defined]
        question=row.question,  # type: ignore[attr-defined]
        phase=row.phase,  # type: ignore[attr-defined]
        duration_hours=int(row.duration_hours),  # type: ignore[attr-defined]
        minutes_per_round=int(row.minutes_per_round),  # type: ignore[attr-defined]
        rounds_mode=row.rounds_mode,  # type: ignore[attr-defined]
        manual_rounds=(
            None
            if row.manual_rounds is None  # type: ignore[attr-defined]
            else int(row.manual_rounds)  # type: ignore[attr-defined]
        ),
        rounds=int(row.rounds),  # type: ignore[attr-defined]
        settings_revision=int(row.settings_revision),  # type: ignore[attr-defined]
        confirmed_settings_revision=(
            None
            if row.confirmed_settings_revision is None  # type: ignore[attr-defined]
            else int(row.confirmed_settings_revision)  # type: ignore[attr-defined]
        ),
        preview_role_count=int(row.preview_role_count),  # type: ignore[attr-defined]
        profile_batch_count=int(row.profile_batch_count),  # type: ignore[attr-defined]
        provider_calls_real=int(row.provider_calls_real),  # type: ignore[attr-defined]
        provider_calls_double=int(row.provider_calls_double),  # type: ignore[attr-defined]
        failure_code=row.failure_code,  # type: ignore[attr-defined]
        failure_message=row.failure_message,  # type: ignore[attr-defined]
        profile_topics=list(row.profile_topics or []),  # type: ignore[attr-defined]
        created_at=row.created_at,  # type: ignore[attr-defined]
        updated_at=row.updated_at,  # type: ignore[attr-defined]
        finished_at=row.finished_at,  # type: ignore[attr-defined]
        job_id=row.job_id,  # type: ignore[attr-defined]
        job_status=row.job_status,  # type: ignore[attr-defined]
        frozen=bool(row.frozen),  # type: ignore[attr-defined]
    )


def _as_role(row: object) -> EnvironmentRoleRow:
    return EnvironmentRoleRow(
        role_id=row.role_id,  # type: ignore[attr-defined]
        preparation_id=row.preparation_id,  # type: ignore[attr-defined]
        snapshot_id=row.snapshot_id,  # type: ignore[attr-defined]
        node_key=row.node_key,  # type: ignore[attr-defined]
        display_name=row.display_name,  # type: ignore[attr-defined]
        role_type=row.role_type,  # type: ignore[attr-defined]
        batch_index=int(row.batch_index),  # type: ignore[attr-defined]
        mention=dict(row.mention or {}),  # type: ignore[attr-defined]
        background=dict(row.background or {}),  # type: ignore[attr-defined]
        expressed_topics=list(row.expressed_topics or []),  # type: ignore[attr-defined]
        involved_topics=list(row.involved_topics or []),  # type: ignore[attr-defined]
        simulation=(
            None
            if row.simulation is None  # type: ignore[attr-defined]
            else dict(row.simulation)  # type: ignore[attr-defined]
        ),
        preview_ready_at=row.preview_ready_at,  # type: ignore[attr-defined]
        published_at=row.published_at,  # type: ignore[attr-defined]
    )


def _parse_uuid(value: str | uuid.UUID, error: type[Exception]) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise error from None


async def _latest_preparation(
    db: AsyncSession, *, project_id: uuid.UUID, build_id: uuid.UUID
) -> EnvironmentPreparation | None:
    result = await db.execute(
        text(
            f"SELECT {PREP_COLUMNS} {PREP_SOURCE} "
            "WHERE p.project_id = :project_id AND p.build_id = :build_id "
            "ORDER BY p.created_at DESC, p.id DESC LIMIT 1"
        ),
        {"project_id": project_id, "build_id": build_id},
    )
    row = result.first()
    return _as_prep(row) if row is not None else None


async def get_preparation(
    db: AsyncSession, *, project_id: uuid.UUID, preparation_id: str | uuid.UUID
) -> EnvironmentPreparation:
    parsed = _parse_uuid(preparation_id, EnvironmentPreparationNotFound)
    try:
        result = await db.execute(
            text(
                f"SELECT {PREP_COLUMNS} {PREP_SOURCE} "
                "WHERE p.id = :preparation_id AND p.project_id = :project_id"
            ),
            {"preparation_id": parsed, "project_id": project_id},
        )
        row = result.first()
    except DB_ERRORS as exc:
        logger.warning("准备详情读取失败: %s", type(exc).__name__)
        raise ServiceUnavailable from exc
    if row is None:
        raise EnvironmentPreparationNotFound
    return _as_prep(row)


async def get_preparation_for_run(
    db: AsyncSession, *, run_id: uuid.UUID
) -> EnvironmentPreparation:
    try:
        result = await db.execute(
            text(f"SELECT {PREP_COLUMNS} {PREP_SOURCE} WHERE p.run_id = :run_id"),
            {"run_id": run_id},
        )
        row = result.first()
    except DB_ERRORS as exc:
        logger.warning("run 准备读取失败: %s", type(exc).__name__)
        raise ServiceUnavailable from exc
    if row is None:
        raise EnvironmentPreparationNotFound
    return _as_prep(row)


def _binding_reason(
    build: graph_store.GraphBuild, snapshot: graph_store.GraphSnapshot | None
) -> str | None:
    if not build.can_continue:
        return "graph_not_ready"
    if snapshot is None:
        return "graph_snapshot_missing"
    if snapshot.build_id != build.id:
        return "graph_snapshot_missing"
    if not snapshot.complete or not snapshot.published:
        return "graph_not_ready"
    return None


async def _newer_build_id(
    db: AsyncSession, *, project_id: uuid.UUID, build_id: uuid.UUID
) -> uuid.UUID | None:
    result = await db.execute(
        text(
            "SELECT id FROM graph_builds WHERE project_id = :project_id "
            "AND (created_at, id) > (SELECT created_at, id FROM graph_builds "
            "WHERE id = :build_id) ORDER BY created_at DESC, id DESC LIMIT 1"
        ),
        {"project_id": project_id, "build_id": build_id},
    )
    value = result.scalar_one_or_none()
    return value


async def read_context(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    build_id: str | uuid.UUID | None,
) -> EnvironmentContext:
    try:
        build, snapshot = await graph_store.read_build_graph(
            db, project_id=project_id, build_id=build_id
        )
    except graph_store.GraphBuildNotFound:
        raise EnvironmentBuildNotFound from None
    except DB_ERRORS as exc:
        logger.warning("准备上下文读取失败: %s", type(exc).__name__)
        raise ServiceUnavailable from exc
    reason = _binding_reason(build, snapshot)
    preparation = await _latest_preparation(
        db, project_id=project_id, build_id=build.id
    )
    newer = await _newer_build_id(db, project_id=project_id, build_id=build.id)
    return EnvironmentContext(
        build=build,
        snapshot=snapshot,
        can_prepare=reason is None,
        reason=reason,
        preparation=preparation,
        newer_build_id=newer,
    )


async def load_bound_snapshot(
    db: AsyncSession, preparation: EnvironmentPreparation
) -> graph_store.GraphSnapshot:
    result = await db.execute(
        text("SELECT build_id, revision FROM graph_snapshots WHERE id = :snapshot_id"),
        {"snapshot_id": preparation.snapshot_id},
    )
    row = result.first()
    if row is None:
        raise EnvironmentConflict("graph_snapshot_missing", "绑定快照不存在")
    return await graph_store.get_published_snapshot(
        db, build_id=row.build_id, revision=int(row.revision)
    )


def select_for_snapshot(snapshot: graph_store.GraphSnapshot) -> RoleSelection:
    return select_roles(snapshot)


async def _read_ready_snapshot(
    db: AsyncSession, *, project_id: uuid.UUID, build_id: uuid.UUID
) -> tuple[graph_store.GraphBuild, graph_store.GraphSnapshot]:
    try:
        build, snapshot = await graph_store.read_build_graph(
            db, project_id=project_id, build_id=build_id
        )
    except graph_store.GraphBuildNotFound:
        raise EnvironmentBuildNotFound from None
    reason = _binding_reason(build, snapshot)
    if reason is not None or snapshot is None:
        raise EnvironmentConflict(reason or "graph_not_ready", "证据图谱尚未完成")
    return build, snapshot


async def start_preparation(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    build_id: str | uuid.UUID,
    config: Any,
    retry: bool,
) -> tuple[EnvironmentPreparation, bool]:
    """普通开始或显式重试；返回 (准备, 是否新建)。"""
    parsed_build = _parse_uuid(build_id, EnvironmentBuildNotFound)
    lock_key = f"{project_id}:prepare:{parsed_build}"
    try:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": lock_key},
        )
        _build, snapshot = await _read_ready_snapshot(
            db, project_id=project_id, build_id=parsed_build
        )
        selection = select_for_snapshot(snapshot)
        if not selection.roles:
            raise EnvironmentConflict(
                "no_role_candidates", "材料中没有可作为角色的主体"
            )
        latest = await _latest_preparation(
            db, project_id=project_id, build_id=parsed_build
        )
        if retry:
            if latest is None or latest.phase not in RETRYABLE_PHASES:
                raise EnvironmentConflict(
                    "preparation_not_retryable", "当前准备不需要重试"
                )
        elif latest is not None:
            if latest.phase in ACTIVE_PHASES:
                await db.commit()
                return latest, False
            raise EnvironmentConflict(
                "preparation_retry_required", "失败或取消的准备需要显式重试"
            )

        preparation_id = uuid.uuid4()
        run_id = uuid.uuid4()
        job_id = uuid.uuid4()
        settings = compute_rounds(
            duration_hours=config.default_duration_hours,
            minutes_per_round=config.default_minutes_per_round,
            rounds_mode="auto",
            manual_rounds=None,
            max_manual_rounds=config.max_manual_rounds,
        )
        await db.execute(
            text(
                "INSERT INTO runs (id, project_id, created_by, kind, stage, "
                "total_steps, fail_at_step, simulated_epoch) "
                "VALUES (:id, :project_id, :created_by, :kind, :stage, "
                ":total_steps, NULL, now())"
            ),
            {
                "id": run_id,
                "project_id": project_id,
                "created_by": actor_id,
                "kind": PREPARE_KIND,
                "stage": PREPARE_STAGE,
                "total_steps": PREPARE_TOTAL_STEPS,
            },
        )
        await db.execute(
            text(
                "INSERT INTO jobs (id, run_id, project_id, operation_key, status, "
                "next_step, attempts, lease_token) "
                "VALUES (:id, :run_id, :project_id, :operation_key, 'queued', 0, 0, NULL)"
            ),
            {
                "id": job_id,
                "run_id": run_id,
                "project_id": project_id,
                "operation_key": (
                    f"{PREPARE_OPERATION_KEY_PREFIX}{parsed_build}"
                ),
            },
        )
        await db.execute(
            text(
                "INSERT INTO environment_preparations (id, project_id, build_id, "
                "snapshot_id, run_id, question, phase, duration_hours, "
                "minutes_per_round, rounds_mode, manual_rounds, rounds) "
                "VALUES (:id, :project_id, :build_id, :snapshot_id, :run_id, "
                ":question, 'preparing', :duration_hours, :minutes_per_round, "
                "'auto', NULL, :rounds)"
            ),
            {
                "id": preparation_id,
                "project_id": project_id,
                "build_id": parsed_build,
                "snapshot_id": snapshot.id,
                "run_id": run_id,
                "question": _build.question,
                "duration_hours": settings.duration_hours,
                "minutes_per_round": settings.minutes_per_round,
                "rounds": settings.rounds,
            },
        )
        await db.commit()
    except EnvironmentConflict:
        await db.rollback()
        raise
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("开始准备冲突 project=%s: %s", project_id, type(exc).__name__)
        existing = await _latest_preparation(
            db, project_id=project_id, build_id=parsed_build
        )
        if existing is not None and existing.phase in ACTIVE_PHASES:
            return existing, False
        raise EnvironmentConflict(
            "preparation_conflict", "准备状态已变化，请刷新后重试"
        ) from exc
    except DB_ERRORS as exc:
        await db.rollback()
        logger.warning("开始准备失败 project=%s: %s", project_id, type(exc).__name__)
        raise ServiceUnavailable from exc
    created = await get_preparation(
        db, project_id=project_id, preparation_id=preparation_id
    )
    return created, True


async def update_configuration(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    preparation_id: str | uuid.UUID,
    settings_revision: int,
    duration_hours: object,
    minutes_per_round: object,
    rounds_mode: object,
    manual_rounds: object,
    config: Any,
) -> EnvironmentPreparation:
    parsed = _parse_uuid(preparation_id, EnvironmentPreparationNotFound)
    try:
        result = await db.execute(
            text(
                "SELECT phase, settings_revision, "
                "EXISTS (SELECT 1 FROM environment_frozen_selections f "
                "        WHERE f.preparation_id = p.id) AS frozen "
                "FROM environment_preparations p "
                "WHERE p.id = :preparation_id AND p.project_id = :project_id "
                "FOR UPDATE"
            ),
            {"preparation_id": parsed, "project_id": project_id},
        )
        row = result.first()
        if row is None:
            await db.rollback()
            raise EnvironmentPreparationNotFound
        if row.frozen:
            await db.rollback()
            raise EnvironmentConflict("preparation_frozen", "准备已确认冻结")
        if row.phase in RETRYABLE_PHASES:
            await db.rollback()
            raise EnvironmentConflict(
                "preparation_not_editable", "失败或取消的准备不可编辑"
            )
        if int(row.settings_revision) != int(settings_revision):
            await db.rollback()
            raise EnvironmentConflict(
                "settings_conflict", "配置已被其他页面更新，请重新读取"
            )
        try:
            settings = compute_rounds(
                duration_hours=duration_hours,
                minutes_per_round=minutes_per_round,
                rounds_mode=rounds_mode,
                manual_rounds=manual_rounds,
                max_manual_rounds=config.max_manual_rounds,
            )
        except EnvironmentContractError as exc:
            await db.rollback()
            raise EnvironmentConflict(exc.code, exc.message) from None
        await db.execute(
            text(
                "UPDATE environment_preparations SET duration_hours = :hours, "
                "minutes_per_round = :minutes, rounds_mode = :mode, "
                "manual_rounds = :manual, rounds = :rounds, "
                "settings_revision = settings_revision + 1, "
                "updated_at = clock_timestamp() WHERE id = :preparation_id"
            ),
            {
                "hours": settings.duration_hours,
                "minutes": settings.minutes_per_round,
                "mode": settings.rounds_mode,
                "manual": settings.manual_rounds,
                "rounds": settings.rounds,
                "preparation_id": parsed,
            },
        )
        await db.commit()
    except (EnvironmentPreparationNotFound, EnvironmentConflict):
        raise
    except DB_ERRORS as exc:
        await db.rollback()
        logger.warning("准备配置保存失败: %s", type(exc).__name__)
        raise ServiceUnavailable from exc
    return await get_preparation(
        db, project_id=project_id, preparation_id=parsed
    )


# ---------------------------------------------------------------- 读取明细


async def load_roles(
    db: AsyncSession, *, preparation: EnvironmentPreparation
) -> list[EnvironmentRoleRow]:
    result = await db.execute(
        text(
            "SELECT role_id, preparation_id, snapshot_id, node_key, display_name, "
            "role_type, batch_index, mention, background, expressed_topics, "
            "involved_topics, simulation, preview_ready_at, published_at "
            "FROM environment_roles WHERE preparation_id = :preparation_id "
            "ORDER BY batch_index, role_id"
        ),
        {"preparation_id": preparation.id},
    )
    return [_as_role(row) for row in result.fetchall()]


async def _load_role_sources(
    db: AsyncSession, *, preparation_id: uuid.UUID
) -> dict[tuple[uuid.UUID, str, int], list[Any]]:
    result = await db.execute(
        text(
            "SELECT role_id, material_field, field_index, source_index, "
            "target_kind, target_key, evidence_index, claim_nature "
            "FROM environment_role_sources WHERE preparation_id = :preparation_id "
            "ORDER BY role_id, material_field, field_index, source_index"
        ),
        {"preparation_id": preparation_id},
    )
    grouped: dict[tuple[uuid.UUID, str, int], list[Any]] = {}
    for row in result.fetchall():
        key = (row.role_id, row.material_field, int(row.field_index))
        grouped.setdefault(key, []).append(row)
    return grouped


async def _load_evidence_map(
    db: AsyncSession, *, snapshot_id: uuid.UUID
) -> dict[tuple[str, str, int], Any]:
    result = await db.execute(
        text(
            "SELECT target_kind, target_key, evidence_index, document_id, "
            "chunk_index, page_no, paragraph_start, paragraph_end, source_start, "
            "source_end, quote FROM graph_evidence WHERE snapshot_id = :snapshot_id"
        ),
        {"snapshot_id": snapshot_id},
    )
    return {
        (row.target_kind, row.target_key, int(row.evidence_index)): row
        for row in result.fetchall()
    }


def _locator(
    *,
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    kind: str,
    name: str,
    page_no: int | None,
    source_start: int,
    source_end: int,
    chunk_index: int,
    paragraph_start: int | None,
    paragraph_end: int | None,
    quote: str,
) -> dict[str, Any]:
    document_path = f"/api/projects/{project_id}/documents/{document_id}"
    body: dict[str, Any] = {
        "documentId": str(document_id),
        "documentName": name,
        "kind": kind,
        "chunkIndex": chunk_index,
        "pageNo": page_no,
        "paragraphStart": paragraph_start,
        "paragraphEnd": paragraph_end,
        "sourceStart": source_start,
        "sourceEnd": source_end,
        "quote": quote,
    }
    if kind == "pdf":
        page = page_no or 1
        body["locator"] = {
            "kind": "pdf",
            "pageNo": page,
            "url": f"{document_path}/file#page={page}",
        }
    else:
        body["locator"] = {
            "kind": "text",
            "url": f"{document_path}/source",
            "sourceStart": source_start,
            "sourceEnd": source_end,
        }
    return body


async def _source_bodies(
    db: AsyncSession,
    *,
    preparation: EnvironmentPreparation,
    grouped: Mapping[tuple[uuid.UUID, str, int], list[Any]],
) -> tuple[
    dict[tuple[uuid.UUID, str, int], list[dict[str, Any]]],
    dict[tuple[str, str, int], dict[str, Any]],
]:
    evidence = await _load_evidence_map(db, snapshot_id=preparation.snapshot_id)
    document_ids = [item.document_id for item in evidence.values()]
    documents = await graph_store.document_kinds(
        db, project_id=preparation.project_id, document_ids=document_ids
    )
    bodies: dict[tuple[uuid.UUID, str, int], list[dict[str, Any]]] = {}
    for key, rows in grouped.items():
        items: list[dict[str, Any]] = []
        for row in rows:
            item = evidence.get(
                (row.target_kind, row.target_key, int(row.evidence_index))
            )
            if item is None:
                continue
            kind, name = documents.get(item.document_id, ("", "材料"))
            items.append(
                _locator(
                    project_id=preparation.project_id,
                    document_id=item.document_id,
                    kind=kind,
                    name=name,
                    page_no=item.page_no,
                    source_start=int(item.source_start),
                    source_end=int(item.source_end),
                    chunk_index=int(item.chunk_index),
                    paragraph_start=item.paragraph_start,
                    paragraph_end=item.paragraph_end,
                    quote=item.quote,
                )
            )
        bodies[key] = items
    flat: dict[tuple[str, str, int], dict[str, Any]] = {}
    for (target_kind, target_key, evidence_index), item in evidence.items():
        kind, name = documents.get(item.document_id, ("", "材料"))
        flat[(target_kind, target_key, evidence_index)] = _locator(
            project_id=preparation.project_id,
            document_id=item.document_id,
            kind=kind,
            name=name,
            page_no=item.page_no,
            source_start=int(item.source_start),
            source_end=int(item.source_end),
            chunk_index=int(item.chunk_index),
            paragraph_start=item.paragraph_start,
            paragraph_end=item.paragraph_end,
            quote=item.quote,
        )
    return bodies, flat


async def source_bodies_for_refs(
    db: AsyncSession,
    *,
    preparation: EnvironmentPreparation,
    refs: Sequence[Any],
) -> list[dict[str, Any]]:
    """把选择器的来源引用转成与证据图谱一致的定位对象。"""
    evidence = await _load_evidence_map(db, snapshot_id=preparation.snapshot_id)
    document_ids = [item.document_id for item in evidence.values()]
    documents = await graph_store.document_kinds(
        db, project_id=preparation.project_id, document_ids=document_ids
    )
    bodies: list[dict[str, Any]] = []
    for ref in refs:
        item = evidence.get(
            (ref.target_kind, ref.target_key, int(ref.evidence_index))
        )
        if item is None:
            continue
        kind, name = documents.get(item.document_id, ("", "材料"))
        bodies.append(
            _locator(
                project_id=preparation.project_id,
                document_id=item.document_id,
                kind=kind,
                name=name,
                page_no=item.page_no,
                source_start=int(item.source_start),
                source_end=int(item.source_end),
                chunk_index=int(item.chunk_index),
                paragraph_start=item.paragraph_start,
                paragraph_end=item.paragraph_end,
                quote=item.quote,
            )
        )
    return bodies


def _role_body(
    role: EnvironmentRoleRow,
    *,
    sources: Mapping[tuple[uuid.UUID, str, int], list[dict[str, Any]]],
) -> dict[str, Any]:
    def field_sources(name: str, index: int) -> list[dict[str, Any]]:
        return list(sources.get((role.role_id, name, index), []))

    mention = dict(role.mention)
    mention["sources"] = field_sources("mention", 0)
    background = dict(role.background)
    background["sources"] = field_sources("background", 0)
    expressed = []
    for index, item in enumerate(role.expressed_topics):
        entry = dict(item)
        entry["sources"] = field_sources("expressedTopics", index)
        expressed.append(entry)
    involved = []
    for index, item in enumerate(role.involved_topics):
        entry = dict(item)
        entry["sources"] = field_sources("involvedTopics", index)
        involved.append(entry)
    simulation = (
        None
        if role.simulation is None
        else {**role.simulation, "origin": "simulation"}
    )
    return {
        "roleId": str(role.role_id),
        "nodeKey": role.node_key,
        "displayName": role.display_name,
        "roleType": role.role_type,
        "batchIndex": role.batch_index,
        "previewReady": role.preview_ready_at is not None,
        "published": role.published_at is not None,
        "mention": mention,
        "background": background,
        "expressedTopics": expressed,
        "involvedTopics": involved,
        "simulation": simulation,
    }


async def load_platforms(
    db: AsyncSession, *, preparation_id: uuid.UUID
) -> list[dict[str, Any]]:
    result = await db.execute(
        text(
            "SELECT platform_key, native_platform, enabled FROM environment_platforms "
            "WHERE preparation_id = :preparation_id ORDER BY platform_key"
        ),
        {"preparation_id": preparation_id},
    )
    return [
        {
            "platformKey": row.platform_key,
            "nativePlatform": row.native_platform,
            "enabled": bool(row.enabled),
        }
        for row in result.fetchall()
    ]


async def load_topics(
    db: AsyncSession, *, preparation_id: uuid.UUID
) -> list[EnvironmentTopic]:
    result = await db.execute(
        text(
            "SELECT topic_id, topic_index, text, origin, claim_nature, "
            "claim_nature_known, sources FROM environment_topics "
            "WHERE preparation_id = :preparation_id ORDER BY topic_index"
        ),
        {"preparation_id": preparation_id},
    )
    return [
        EnvironmentTopic(
            topic_id=row.topic_id,
            topic_index=int(row.topic_index),
            text=row.text,
            origin=row.origin,
            claim_nature=row.claim_nature,
            claim_nature_known=bool(row.claim_nature_known),
            sources=list(row.sources or []),
        )
        for row in result.fetchall()
    ]


async def load_activation(
    db: AsyncSession, *, preparation_id: uuid.UUID
) -> list[EnvironmentActivation]:
    result = await db.execute(
        text(
            "SELECT position, role_id, is_first_speaker, first_topic_id "
            "FROM environment_activation WHERE preparation_id = :preparation_id "
            "ORDER BY position"
        ),
        {"preparation_id": preparation_id},
    )
    return [
        EnvironmentActivation(
            position=int(row.position),
            role_id=row.role_id,
            is_first_speaker=bool(row.is_first_speaker),
            first_topic_id=row.first_topic_id,
        )
        for row in result.fetchall()
    ]


async def load_frozen_selection(
    db: AsyncSession, *, preparation_id: uuid.UUID
) -> dict[str, Any] | None:
    result = await db.execute(
        text(
            "SELECT payload FROM environment_frozen_selections "
            "WHERE preparation_id = :preparation_id"
        ),
        {"preparation_id": preparation_id},
    )
    row = result.first()
    return None if row is None else dict(row.payload)


def _activation_body(
    activation: Sequence[EnvironmentActivation],
) -> dict[str, Any]:
    first = [item for item in activation if item.is_first_speaker]
    return {
        "firstSpeakerRoleIds": [str(item.role_id) for item in first],
        "firstSpeakerTopics": [
            {"roleId": str(item.role_id), "topicId": item.first_topic_id}
            for item in first
            if item.first_topic_id is not None
        ],
        "order": [str(item.role_id) for item in activation],
    }


def _settings_body(preparation: EnvironmentPreparation) -> dict[str, Any]:
    settings = preparation.settings
    return {
        "durationHours": settings.duration_hours,
        "minutesPerRound": settings.minutes_per_round,
        "roundsMode": settings.rounds_mode,
        "manualRounds": settings.manual_rounds,
        "rounds": settings.rounds,
        "autoRounds": settings.auto_rounds,
        "settingsRevision": preparation.settings_revision,
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _base_body(preparation: EnvironmentPreparation, *, build: Any) -> dict[str, Any]:
    return {
        "preparationId": str(preparation.id),
        "projectId": str(preparation.project_id),
        "buildId": str(preparation.build_id),
        "snapshotId": str(preparation.snapshot_id),
        "runId": str(preparation.run_id),
        "jobId": str(preparation.job_id),
        "jobStatus": preparation.job_status,
        "question": preparation.question,
        "phase": preparation.phase,
        "settings": _settings_body(preparation),
        "failure": (
            None
            if preparation.failure_code is None
            else {
                "code": preparation.failure_code,
                "message": preparation.failure_message or "准备未完成",
            }
        ),
        "counts": {
            "previewRoleCount": preparation.preview_role_count,
            "profileBatchCount": preparation.profile_batch_count,
            "providerCalls": {
                "real": preparation.provider_calls_real,
                "double": preparation.provider_calls_double,
            },
        },
        "createdAt": _iso(preparation.created_at),
        "updatedAt": _iso(preparation.updated_at),
        "finishedAt": _iso(preparation.finished_at),
        "graph": {
            "phase": build.phase,
            "canContinue": build.can_continue,
            "question": build.question,
        },
    }


async def serialize_preparation(
    db: AsyncSession, preparation: EnvironmentPreparation
) -> dict[str, Any]:
    build, snapshot = await graph_store.read_build_graph(
        db, project_id=preparation.project_id, build_id=preparation.build_id
    )
    roles = await load_roles(db, preparation=preparation)
    grouped = await _load_role_sources(db, preparation_id=preparation.id)
    bodies, _flat = await _source_bodies(
        db, preparation=preparation, grouped=grouped
    )
    frozen = await load_frozen_selection(db, preparation_id=preparation.id)
    final_roles = [
        role
        for role in roles
        if role.published_at is not None and role.simulation is not None
    ]
    preview_roles = [
        role
        for role in roles
        if role.preview_ready_at is not None and role.published_at is None
    ]
    body = _base_body(preparation, build=build)
    body["snapshot"] = (
        None
        if snapshot is None
        else {
            "snapshotId": str(snapshot.id),
            "buildId": str(snapshot.build_id),
            "revision": snapshot.revision,
            "complete": snapshot.complete,
            "published": snapshot.published,
            "nodeCount": snapshot.node_count,
            "edgeCount": snapshot.edge_count,
        }
    )
    body["progress"] = {
        "totalRoles": len(roles),
        "profiledRoles": len(
            [role for role in roles if role.preview_ready_at is not None]
        ),
        "publishedRoles": len(final_roles),
        "previewRoleCount": len(preview_roles),
        "batchCount": len({role.batch_index for role in roles}),
        "previewBatchCount": len({role.batch_index for role in preview_roles}),
    }
    body["previewOnly"] = preparation.phase != "ready"
    body["previewRoles"] = (
        [
            _role_body(role, sources=bodies)
            for role in preview_roles
        ]
        if preparation.phase != "ready"
        else []
    )
    body["roles"] = (
        [_role_body(role, sources=bodies) for role in final_roles]
        if preparation.phase == "ready"
        else []
    )
    if preparation.phase == "ready" and final_roles:
        body["platforms"] = await load_platforms(db, preparation_id=preparation.id)
        topics = await load_topics(db, preparation_id=preparation.id)
        body["initialTopics"] = [
            {
                "topicId": topic.topic_id,
                "text": topic.text,
                "origin": topic.origin,
                "claimNature": topic.claim_nature,
                "claimNatureKnown": topic.claim_nature_known,
                "sources": topic.sources,
            }
            for topic in topics
        ]
        activation = await load_activation(db, preparation_id=preparation.id)
        body["activation"] = _activation_body(activation)
    else:
        body["platforms"] = []
        body["initialTopics"] = []
        body["activation"] = None
    body["frozenSelection"] = frozen
    body["canContinue"] = frozen is not None
    body["requiredAction"] = (
        "confirm"
        if preparation.phase == "ready" and frozen is None
        else "retry"
        if preparation.phase in RETRYABLE_PHASES
        else None
    )
    return body


def context_body(context: EnvironmentContext) -> dict[str, Any]:
    build = context.build
    snapshot = context.snapshot
    preparation = context.preparation
    return {
        "build": {
            "buildId": str(build.id),
            "projectId": str(build.project_id),
            "runId": str(build.run_id),
            "jobId": str(build.job_id),
            "question": build.question,
            "phase": build.phase,
            "canContinue": build.can_continue,
            "failure": (
                None
                if build.failure_code is None
                else {"code": build.failure_code, "message": build.failure_message}
            ),
        },
        "snapshot": (
            None
            if snapshot is None
            else {
                "snapshotId": str(snapshot.id),
                "revision": snapshot.revision,
                "complete": snapshot.complete,
                "published": snapshot.published,
                "nodeCount": snapshot.node_count,
                "edgeCount": snapshot.edge_count,
            }
        ),
        "canPrepare": context.can_prepare,
        "reason": context.reason,
        "preparation": (
            None
            if preparation is None
            else {
                "preparationId": str(preparation.id),
                "jobId": str(preparation.job_id),
                "runId": str(preparation.run_id),
                "phase": preparation.phase,
                "settings": _settings_body(preparation),
                "failure": (
                    None
                    if preparation.failure_code is None
                    else {
                        "code": preparation.failure_code,
                        "message": preparation.failure_message,
                    }
                ),
                "frozen": preparation.frozen,
                "previewRoleCount": preparation.preview_role_count,
                "profileBatchCount": preparation.profile_batch_count,
            }
        ),
        "newerBuildId": (
            None if context.newer_build_id is None else str(context.newer_build_id)
        ),
    }


# ---------------------------------------------------------------- 冻结校验


async def _validated_publish_payload(
    db: AsyncSession, preparation: EnvironmentPreparation
) -> dict[str, Any]:
    if preparation.phase != "ready":
        raise EnvironmentConflict("preparation_not_ready", "准备尚未完成")
    try:
        settings = compute_rounds(
            duration_hours=preparation.duration_hours,
            minutes_per_round=preparation.minutes_per_round,
            rounds_mode=preparation.rounds_mode,
            manual_rounds=preparation.manual_rounds,
            max_manual_rounds=1000,
        )
    except EnvironmentContractError as exc:
        raise EnvironmentConflict(exc.code, exc.message) from None
    roles = await load_roles(db, preparation=preparation)
    published = [
        role for role in roles if role.published_at is not None and role.simulation
    ]
    if len(published) != len(roles) or not published:
        raise EnvironmentConflict("preparation_not_ready", "最终角色尚未完整发布")
    grouped = await _load_role_sources(db, preparation_id=preparation.id)
    bodies, _flat = await _source_bodies(
        db, preparation=preparation, grouped=grouped
    )
    for role in published:
        if not bodies.get((role.role_id, "mention", 0)):
            raise EnvironmentConflict(
                "role_source_missing", "角色缺少材料来源"
            )
        try:
            validate_simulation(role.simulation)
        except EnvironmentContractError as exc:
            raise EnvironmentConflict(exc.code, exc.message) from None
    platforms = await load_platforms(db, preparation_id=preparation.id)
    mapping = {
        item["platformKey"]: item["nativePlatform"] for item in platforms
    }
    if mapping != dict(PLATFORM_MAPPING):
        raise EnvironmentConflict(
            "platform_mapping_invalid", "平台映射不完整"
        )
    topics = await load_topics(db, preparation_id=preparation.id)
    if not topics or not any(topic.origin == "question" for topic in topics):
        raise EnvironmentConflict("topic_missing", "缺少推演问题话题")
    for topic in topics:
        if topic.origin == "material" and not topic.sources:
            raise EnvironmentConflict(
                "topic_source_missing", "材料话题缺少来源"
            )
        if topic.claim_nature_known and topic.claim_nature is None:
            raise EnvironmentConflict("topic_source_missing", "话题来源性质缺失")
    activation = await load_activation(db, preparation_id=preparation.id)
    role_ids = [str(role.role_id) for role in published]
    order = [str(item.role_id) for item in activation]
    if len(order) != len(role_ids) or set(order) != set(role_ids):
        raise EnvironmentConflict(
            "activation_order_invalid", "激活顺序必须覆盖全部角色"
        )
    first = [str(item.role_id) for item in activation if item.is_first_speaker]
    if not first or order[: len(first)] != first:
        raise EnvironmentConflict(
            "activation_order_invalid", "首发角色必须是激活顺序前缀"
        )
    topic_ids = {topic.topic_id for topic in topics}
    for item in activation:
        if item.is_first_speaker and item.first_topic_id not in topic_ids:
            raise EnvironmentConflict(
                "first_topic_missing", "首发角色缺少对应话题"
            )
        if not item.is_first_speaker and item.first_topic_id is not None:
            raise EnvironmentConflict(
                "first_topic_missing", "非首发角色不能带首发话题"
            )

    role_by_id = {str(role.role_id): role for role in published}
    ordered_published = [role_by_id[role_id] for role_id in order]
    roles_body = [
        _role_body(role, sources=bodies) for role in ordered_published
    ]
    return {
        "selectionId": None,
        "preparationId": str(preparation.id),
        "projectId": str(preparation.project_id),
        "buildId": str(preparation.build_id),
        "snapshotId": str(preparation.snapshot_id),
        "question": preparation.question,
        "settingsRevision": preparation.settings_revision,
        "settings": {
            "durationHours": settings.duration_hours,
            "minutesPerRound": settings.minutes_per_round,
            "roundsMode": settings.rounds_mode,
            "manualRounds": settings.manual_rounds,
            "rounds": settings.rounds,
            "autoRounds": settings.auto_rounds,
        },
        "roles": [
            {
                key: value
                for key, value in role.items()
                if key
                in {
                    "roleId",
                    "nodeKey",
                    "displayName",
                    "roleType",
                    "mention",
                    "background",
                    "expressedTopics",
                    "involvedTopics",
                    "simulation",
                }
            }
            for role in roles_body
        ],
        "platforms": [
            {
                "platformKey": item["platformKey"],
                "nativePlatform": item["nativePlatform"],
            }
            for item in platforms
        ],
        "initialTopics": [
            {
                "topicId": topic.topic_id,
                "text": topic.text,
                "origin": topic.origin,
                "claimNature": topic.claim_nature,
                "claimNatureKnown": topic.claim_nature_known,
                "sources": topic.sources,
            }
            for topic in topics
        ],
        "activation": _activation_body(activation),
    }


async def confirm_preparation(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    preparation_id: str | uuid.UUID,
    settings_revision: int,
) -> tuple[EnvironmentPreparation, dict[str, Any]]:
    parsed = _parse_uuid(preparation_id, EnvironmentPreparationNotFound)
    try:
        result = await db.execute(
            text(
                "SELECT id FROM environment_preparations "
                "WHERE id = :preparation_id AND project_id = :project_id "
                "FOR UPDATE"
            ),
            {"preparation_id": parsed, "project_id": project_id},
        )
        if result.first() is None:
            await db.rollback()
            raise EnvironmentPreparationNotFound
        preparation = await get_preparation(
            db, project_id=project_id, preparation_id=parsed
        )
        existing = await load_frozen_selection(
            db, preparation_id=preparation.id
        )
        if existing is not None:
            await db.commit()
            if int(existing.get("settingsRevision", -1)) == int(settings_revision):
                return preparation, existing
            raise EnvironmentConflict(
                "settings_conflict", "准备已按其他配置确认冻结"
            )
        if preparation.phase != "ready":
            await db.rollback()
            raise EnvironmentConflict("preparation_not_ready", "准备尚未完成")
        if preparation.settings_revision != int(settings_revision):
            await db.rollback()
            raise EnvironmentConflict(
                "settings_conflict", "配置已被其他页面更新，请重新读取"
            )
        payload = await _validated_publish_payload(db, preparation)
        selection_id = uuid.uuid4()
        payload["selectionId"] = str(selection_id)
        await db.execute(
            text(
                "INSERT INTO environment_frozen_selections (selection_id, "
                "preparation_id, project_id, build_id, snapshot_id, "
                "settings_revision, payload) VALUES (:selection_id, "
                ":preparation_id, :project_id, :build_id, :snapshot_id, "
                ":settings_revision, CAST(:payload AS jsonb))"
            ),
            {
                "selection_id": selection_id,
                "preparation_id": preparation.id,
                "project_id": preparation.project_id,
                "build_id": preparation.build_id,
                "snapshot_id": preparation.snapshot_id,
                "settings_revision": preparation.settings_revision,
                "payload": json.dumps(payload, ensure_ascii=False),
            },
        )
        await db.execute(
            text(
                "UPDATE environment_preparations SET "
                "confirmed_settings_revision = :revision, "
                "updated_at = clock_timestamp() WHERE id = :preparation_id"
            ),
            {
                "revision": preparation.settings_revision,
                "preparation_id": preparation.id,
            },
        )
        await db.commit()
    except (EnvironmentPreparationNotFound, EnvironmentConflict):
        raise
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("准备确认冲突: %s", type(exc).__name__)
        preparation = await get_preparation(
            db, project_id=project_id, preparation_id=parsed
        )
        existing = await load_frozen_selection(
            db, preparation_id=preparation.id
        )
        if existing is not None and int(
            existing.get("settingsRevision", -1)
        ) == int(settings_revision):
            return preparation, existing
        raise EnvironmentConflict(
            "settings_conflict", "配置已被其他页面更新，请重新读取"
        ) from exc
    except DB_ERRORS as exc:
        await db.rollback()
        logger.warning("准备确认失败: %s", type(exc).__name__)
        raise ServiceUnavailable from exc
    preparation = await get_preparation(
        db, project_id=project_id, preparation_id=parsed
    )
    return preparation, payload


async def cancel_for_run(db: AsyncSession, *, run_id: uuid.UUID) -> None:
    """在取消事务内把准备置为 canceled；不发布任何最终配置。"""
    await db.execute(
        text(
            "UPDATE environment_preparations SET phase = 'canceled', "
            "failure_code = COALESCE(failure_code, 'canceled'), "
            "failure_message = COALESCE(failure_message, '准备已取消'), "
            "finished_at = clock_timestamp(), updated_at = clock_timestamp() "
            "WHERE run_id = :run_id AND phase = 'preparing'"
        ),
        {"run_id": run_id},
    )
