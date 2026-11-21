"""引擎依赖四阶段基线：解析、安装、导入与最小运行。"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

PINS = {
    "camel-oasis": "0.2.6",
    "camel-ai": "0.2.90",
    "zep-cloud": "4.1.0",
}
IMPORT_MODULES = {
    "camel-oasis": "oasis",
    "camel-ai": "camel",
    "zep-cloud": "zep_cloud",
}
DEFAULT_RESOLVE_PATH = Path("/app/engine-resolve.json")
DEFAULT_BUILD_PATH = Path("/app/engine-build.json")


@dataclass
class StageResult:
    status: str
    detail: str
    duration_ms: int = 0
    versions: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "detail": self.detail,
            "durationMs": self.duration_ms,
            "versions": self.versions,
        }


@dataclass
class DependencyBaseline:
    status: str
    stages: dict[str, StageResult]
    pins: dict[str, str]
    target: str
    python: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "target": self.target,
            "python": self.python,
            "pins": self.pins,
            "stages": {name: stage.to_dict() for name, stage in self.stages.items()},
            "detail": self.detail,
        }


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _resolve_versions(report: dict[str, Any] | None) -> dict[str, str]:
    versions: dict[str, str] = {}
    if not report:
        return versions
    for entry in report.get("install", []) or []:
        metadata = entry.get("metadata") if isinstance(entry, dict) else None
        if not isinstance(metadata, dict):
            continue
        name = str(metadata.get("name", "")).lower()
        version = str(metadata.get("version", ""))
        if name in PINS:
            versions[name] = version
    return versions


def resolve_stage(path: Path) -> StageResult:
    started = time.monotonic()
    report = _read_json(path)
    duration = int((time.monotonic() - started) * 1000)
    if report is None:
        return StageResult("not_run", f"缺少解析证据：{path}", duration, {})
    versions = _resolve_versions(report)
    if any(name not in versions for name in PINS):
        return StageResult("failed", "解析结果缺少候选包", duration, versions)
    mismatched = {
        name: versions[name] for name, expected in PINS.items() if versions[name] != expected
    }
    if mismatched:
        return StageResult("failed", f"解析版本不一致：{mismatched}", duration, versions)
    return StageResult("passed", "解析出候选版本", duration, versions)


def install_stage(path: Path) -> StageResult:
    started = time.monotonic()
    record = _read_json(path)
    duration = int((time.monotonic() - started) * 1000)
    if record is None:
        return StageResult("not_run", f"缺少安装证据：{path}", duration, {})
    install = record.get("install") if isinstance(record, dict) else None
    if not isinstance(install, dict):
        return StageResult("failed", "安装证据缺 install 字段", duration, {})
    status = str(install.get("status", "failed"))
    versions = install.get("versions") if isinstance(install.get("versions"), dict) else {}
    return StageResult(status, str(install.get("detail", "")), duration, dict(versions))


def import_stage() -> StageResult:
    started = time.monotonic()
    versions: dict[str, str] = {}
    try:
        for package, module in IMPORT_MODULES.items():
            importlib.import_module(module)
            versions[package] = importlib.metadata.version(package)
    except Exception as exc:  # noqa: BLE001 - 导入失败记录类型
        return StageResult(
            "failed",
            f"{type(exc).__name__}: {exc}",
            int((time.monotonic() - started) * 1000),
            versions,
        )
    mismatched = {
        name: versions[name] for name, expected in PINS.items() if versions.get(name) != expected
    }
    duration = int((time.monotonic() - started) * 1000)
    if mismatched:
        return StageResult("failed", f"导入版本不一致：{mismatched}", duration, versions)
    return StageResult("passed", "候选包可导入", duration, versions)


def minimal_stage(
    runner: Callable[[Path], tuple[bool, str]] | None = None,
) -> StageResult:
    started = time.monotonic()
    if runner is None:
        from .simulation import minimal_simulation_check

        runner = minimal_simulation_check
    with tempfile.TemporaryDirectory(prefix="echosim-engine-minimal-") as tmp:
        ok, detail = runner(Path(tmp))
    return StageResult(
        "passed" if ok else "failed",
        detail,
        int((time.monotonic() - started) * 1000),
        {},
    )


def evaluate(
    *,
    resolve_path: Path | None = None,
    build_path: Path | None = None,
    minimal_runner: Callable[[Path], tuple[bool, str]] | None = None,
) -> DependencyBaseline:
    stages = {
        "resolve": resolve_stage(resolve_path or DEFAULT_RESOLVE_PATH),
        "install": install_stage(build_path or DEFAULT_BUILD_PATH),
        "import": import_stage(),
        "minimalRun": minimal_stage(minimal_runner),
    }
    verified = all(stage.status == "passed" for stage in stages.values())
    failed = [name for name, stage in stages.items() if stage.status != "passed"]
    return DependencyBaseline(
        "verified" if verified else "unverified",
        stages,
        dict(PINS),
        f"linux-{platform.machine()}",
        platform.python_version(),
        "" if verified else f"未通过阶段：{', '.join(failed)}",
    )


def record_build(path: Path) -> int:
    """在引擎镜像构建时记录安装与导入证据。"""
    versions: dict[str, str] = {}
    old_cwd = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="echosim-engine-build-") as tmp:
        try:
            os.chdir(tmp)
            import_result = import_stage()
        finally:
            os.chdir(old_cwd)
    versions.update(import_result.versions)
    pip_check = subprocess.run(
        [sys.executable, "-m", "pip", "check"],
        capture_output=True,
        text=True,
        check=False,
    )
    install_ok = import_result.status == "passed" and pip_check.returncode == 0
    record = {
        "target": f"linux-{platform.machine()}",
        "python": platform.python_version(),
        "install": {
            "status": "passed" if install_ok else "failed",
            "detail": "pip install 完成且 pip check 通过"
            if install_ok
            else (pip_check.stdout + pip_check.stderr).strip()[:2000],
            "versions": versions,
        },
        "import": import_result.to_dict(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if install_ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EchoSim 引擎依赖四阶段基线")
    parser.add_argument("--record-build", type=Path, help="记录镜像安装与导入证据")
    parser.add_argument("--json", action="store_true", help="输出四阶段评估 JSON")
    args = parser.parse_args(argv)
    if args.record_build:
        return record_build(args.record_build)
    baseline = evaluate()
    print(json.dumps(baseline.to_dict(), ensure_ascii=False, indent=2))
    return 0 if baseline.status == "verified" else 1


if __name__ == "__main__":
    sys.exit(main())
