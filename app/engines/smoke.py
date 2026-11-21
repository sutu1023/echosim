"""真实联通验收编排：逐项状态、调用计数、报告与退出码。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .baseline import DependencyBaseline, evaluate as evaluate_baseline
from .contracts import (
    AcceptanceMaterial,
    CallCounts,
    ItemResult,
    PASSED,
    FAILED,
    NOT_RUN,
    build_acceptance_material,
    merge_calls,
    new_run_id,
)
from .graph import GraphRunner
from .model import ModelRunner
from .settings import EngineSettings
from .simulation import (
    PlatformOutcome,
    SharedSetupError,
    run_platform,
)

REQUIRED_ITEMS = (
    "model.structured",
    "graph.accepted",
    "graph.searchable",
    "simulation.platform_a",
    "simulation.platform_b",
    "cleanup",
    "dependencyBaseline",
)


@dataclass(frozen=True)
class SmokeReport:
    report: dict[str, Any]
    exit_code: int


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def write_report(report_dir: Path, report: dict[str, Any]) -> None:
    _atomic_write(report_dir / "last-run.json", report)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    _atomic_write(report_dir / f"run-{stamp}.json", report)


def _baseline_item(baseline: DependencyBaseline) -> ItemResult:
    passed = baseline.status == "verified"
    return ItemResult(
        "dependencyBaseline",
        "dependencies",
        "four-stage",
        PASSED if passed else FAILED,
        None if passed else "dependency_unresolved",
        0,
        CallCounts(),
        baseline.to_dict(),
    )


def _not_run(item_id: str, dependency: str, stage: str, reason: str) -> ItemResult:
    return ItemResult(item_id, dependency, stage, NOT_RUN, reason, 0, CallCounts(), {})


def _graph_items(result: ItemResult) -> list[ItemResult]:
    accepted = bool(result.evidence.get("accepted"))
    accepted_item = ItemResult(
        "graph.accepted",
        "graph",
        "episode-write",
        PASSED if accepted else result.status,
        None if accepted else result.reason,
        result.duration_ms,
        CallCounts(),
        {
            "episodeId": result.evidence.get("episodeId"),
            "idempotencyKey": result.evidence.get("idempotencyKey"),
            "writeResponseLost": result.evidence.get("writeResponseLost"),
            "firstSearchMiss": result.evidence.get("firstSearchMiss"),
        },
    )
    return [accepted_item, result]


def _cleanup_item(outcomes: list[PlatformOutcome]) -> ItemResult:
    if not outcomes:
        return _not_run("cleanup", "simulation", "cleanup", "prerequisite_failed")
    statuses = [outcome.cleanup for outcome in outcomes]
    if any(status == "failed" for status in statuses):
        return ItemResult(
            "cleanup",
            "simulation",
            "cleanup",
            FAILED,
            "cleanup_failed",
            0,
            CallCounts(),
            {"platforms": statuses},
        )
    if any(outcome.item.status == NOT_RUN for outcome in outcomes):
        return ItemResult(
            "cleanup",
            "simulation",
            "cleanup",
            NOT_RUN,
            "prerequisite_failed",
            0,
            CallCounts(),
            {"platforms": statuses},
        )
    return ItemResult(
        "cleanup",
        "simulation",
        "cleanup",
        PASSED,
        None,
        0,
        CallCounts(),
        {"platforms": statuses},
    )


def _build_report(
    *,
    mode: str,
    tested_sha: str,
    dirty: bool,
    run_id: str,
    material: AcceptanceMaterial,
    settings: EngineSettings,
    baseline: DependencyBaseline | None,
    items: list[ItemResult],
    started_at: str,
    exit_code: int,
    reason: str | None = None,
) -> dict[str, Any]:
    required = {item.item_id: item for item in items if item.item_id in REQUIRED_ITEMS}
    all_required_passed = len(required) == len(REQUIRED_ITEMS) and all(
        required[item_id].status == PASSED for item_id in REQUIRED_ITEMS
    )
    result = "passed" if all_required_passed and exit_code == 0 else "failed"
    calls = merge_calls(items)
    cleanup = {
        "platform_a": next(
            (item.evidence.get("cleanup") for item in items if item.item_id == "simulation.platform_a"),
            None,
        ),
        "platform_b": next(
            (item.evidence.get("cleanup") for item in items if item.item_id == "simulation.platform_b"),
            None,
        ),
    }
    return {
        "schemaVersion": 1,
        "result": result,
        "mode": mode,
        "tested_sha": tested_sha,
        "dirty": dirty,
        "runId": run_id,
        "materialId": material.material_id,
        "startedAt": started_at,
        "finishedAt": utc_now(),
        "settings": settings.public_dict(),
        "dependencyBaseline": baseline.to_dict() if baseline else None,
        "items": [item.to_dict() for item in items],
        "calls": calls.to_dict(),
        "cleanup": cleanup,
        "exitCode": exit_code,
        "reason": reason,
    }


def run_smoke(
    *,
    settings: EngineSettings,
    report_dir: Path,
    tested_sha: str,
    dirty: bool,
    allow_dirty: bool = False,
    mode: str = "live",
    run_id: str | None = None,
    baseline_evaluator: Callable[[], DependencyBaseline] = evaluate_baseline,
    model_runner: Callable[[AcceptanceMaterial], ItemResult] | None = None,
    graph_runner: Callable[[AcceptanceMaterial], ItemResult] | None = None,
    platform_runner: Callable[[str, EngineSettings, AcceptanceMaterial, Path], PlatformOutcome]
    | None = None,
) -> SmokeReport:
    started_at = utc_now()
    actual_run_id = run_id or new_run_id()
    material = build_acceptance_material(actual_run_id)
    report_dir.mkdir(parents=True, exist_ok=True)

    if dirty and not allow_dirty:
        reason = "dirty_worktree"
        item = _not_run("worktree", "repository", "preflight", reason)
        report = _build_report(
            mode=mode,
            tested_sha=tested_sha,
            dirty=True,
            run_id=actual_run_id,
            material=material,
            settings=settings,
            baseline=None,
            items=[item],
            started_at=started_at,
            exit_code=2,
            reason=reason,
        )
        write_report(report_dir, report)
        return SmokeReport(report, 2)

    baseline = baseline_evaluator()
    items: list[ItemResult] = [_baseline_item(baseline)]
    if baseline.status != "verified":
        items.extend(
            [
                _not_run("model.structured", "model", "structured-extraction", "dependency_unresolved"),
                _not_run("graph.accepted", "graph", "episode-write", "dependency_unresolved"),
                _not_run("graph.searchable", "graph", "episode-search", "dependency_unresolved"),
                _not_run("simulation.platform_a", "simulation", "create_post", "dependency_unresolved"),
                _not_run("simulation.platform_b", "simulation", "create_comment", "dependency_unresolved"),
                _not_run("cleanup", "simulation", "cleanup", "dependency_unresolved"),
            ]
        )
        report = _build_report(
            mode=mode,
            tested_sha=tested_sha,
            dirty=dirty,
            run_id=actual_run_id,
            material=material,
            settings=settings,
            baseline=baseline,
            items=items,
            started_at=started_at,
            exit_code=1,
        )
        write_report(report_dir, report)
        return SmokeReport(report, 1)

    if settings.missing_model:
        items.append(_not_run("model.structured", "model", "structured-extraction", "missing_config"))
    else:
        items.append((model_runner or ModelRunner(settings).run)(material))

    if settings.missing_graph:
        items.extend(
            [
                _not_run("graph.accepted", "graph", "episode-write", "missing_config"),
                _not_run("graph.searchable", "graph", "episode-search", "missing_config"),
            ]
        )
    else:
        graph_result = (graph_runner or GraphRunner(settings).run)(material)
        items.extend(_graph_items(graph_result))

    platform_results: list[PlatformOutcome] = []
    try:
        runner = platform_runner or (
            lambda key, cfg, mat, root: run_platform(key, cfg, mat, root)
        )
        work_root = report_dir / f"work-{actual_run_id}"
        try:
            platform_results.append(runner("platform_a", settings, material, work_root))
            platform_results.append(runner("platform_b", settings, material, work_root))
        finally:
            shutil.rmtree(work_root, ignore_errors=True)
    except SharedSetupError:
        items.extend(
            [
                _not_run("simulation.platform_a", "simulation", "create_post", "shared_setup"),
                _not_run("simulation.platform_b", "simulation", "create_comment", "shared_setup"),
            ]
        )
        platform_results = []
    else:
        items.extend(outcome.item for outcome in platform_results)

    items.append(_cleanup_item(platform_results))

    required = {item.item_id: item for item in items if item.item_id in REQUIRED_ITEMS}
    exit_code = 0 if len(required) == len(REQUIRED_ITEMS) and all(
        required[item_id].status == PASSED for item_id in REQUIRED_ITEMS
    ) else 1
    report = _build_report(
        mode=mode,
        tested_sha=tested_sha,
        dirty=dirty,
        run_id=actual_run_id,
        material=material,
        settings=settings,
        baseline=baseline,
        items=items,
        started_at=started_at,
        exit_code=exit_code,
    )
    write_report(report_dir, report)
    return SmokeReport(report, exit_code)


def _git_value(args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", *args], capture_output=True, text=True, check=False, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EchoSim 真实联通验收")
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path(os.environ.get("SMOKE_REPORT_DIR", "runtime/smoke-live")),
    )
    args = parser.parse_args(argv)
    settings = EngineSettings.from_env()
    tested_sha = os.environ.get("ECHOSIM_TESTED_SHA") or _git_value(["rev-parse", "HEAD"]) or "unknown"
    dirty_env = os.environ.get("ECHOSIM_DIRTY")
    if dirty_env is None:
        dirty = bool(_git_value(["status", "--porcelain"]))
    else:
        dirty = dirty_env.strip().lower() in {"1", "true", "yes"}
    allow_dirty = os.environ.get("ALLOW_DIRTY", "").strip().lower() in {"1", "true", "yes"}
    outcome = run_smoke(
        settings=settings,
        report_dir=args.report_dir,
        tested_sha=tested_sha,
        dirty=dirty,
        allow_dirty=allow_dirty,
    )
    print(
        f"[smoke-live] result={outcome.report['result']} "
        f"mode={outcome.report['mode']} tested_sha={tested_sha} dirty={dirty}"
    )
    print(f"[smoke-live] 报告：{args.report_dir / 'last-run.json'}")
    for item in outcome.report["items"]:
        print(
            f"  - {item['id']}: {item['status']} "
            f"({item['durationMs']}ms) {item.get('reason') or ''}"
        )
    return outcome.exit_code


if __name__ == "__main__":
    sys.exit(main())
