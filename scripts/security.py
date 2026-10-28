import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from vulnerability_policy import classify

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts"


def execute(args, expect=0):
    result = subprocess.run(args, cwd=ROOT, check=False)
    if result.returncode != expect:
        raise SystemExit(f"security check exited {result.returncode}, expected {expect}")


def main():
    OUT.mkdir(exist_ok=True)
    images = dict(
        line.split("=", 1)
        for line in (ROOT / "infra/images.env").read_text().splitlines()
        if line and not line.startswith("#")
    )
    if sys.argv[1] == "secrets":
        image = images["GITLEAKS_IMAGE"]
        base = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{ROOT}:/repo:ro",
            "-v",
            f"{OUT}:/reports",
            image,
        ]
        with tempfile.TemporaryDirectory(prefix="echosim-worktree-") as folder:
            names = (
                subprocess.check_output(
                    [
                        "git",
                        "ls-files",
                        "-z",
                        "--cached",
                        "--others",
                        "--exclude-standard",
                    ],
                    cwd=ROOT,
                )
                .decode()
                .split("\0")
            )
            for name in set(names):
                if not name:
                    continue
                source = ROOT / name
                target = Path(folder) / name
                target.parent.mkdir(parents=True, exist_ok=True)
                if source.is_symlink():
                    target.write_text(os.readlink(source))
                elif source.is_file():
                    shutil.copyfile(source, target)
            execute(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{folder}:/repo:ro",
                    "-v",
                    f"{OUT}:/reports",
                    image,
                    "dir",
                    "/repo",
                    "--config=/repo/.gitleaks.toml",
                    "--redact=100",
                    "--no-banner",
                    "--report-format=json",
                    "--report-path=/reports/gitleaks-worktree.json",
                ]
            )
        execute(
            [
                *base,
                "git",
                "/repo",
                "--config=/repo/.gitleaks.toml",
                "--redact=100",
                "--no-banner",
                "--log-opts=--all",
                "--report-format=json",
                "--report-path=/reports/gitleaks-history.json",
            ]
        )
        # SC-GATE-02: a constructed, non-functional PAT fixture must trip the real scanner.
        with tempfile.TemporaryDirectory(prefix="echosim-secret-") as folder:
            Path(folder, "fixture.txt").write_text(
                'token = "' + "gh" + "p_" + secrets.token_hex(18) + '"\n'
            )
            execute(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{folder}:/fixture:ro",
                    image,
                    "dir",
                    "/fixture",
                    "--redact=100",
                    "--no-banner",
                ],
                expect=1,
            )
        print("Secret-negative fixture correctly blocked; reports redacted.")
    elif sys.argv[1] == "vulnerabilities":
        # SC-GATE-05: scanner/database failures remain nonzero, no ignore-unfixed bypass.
        image = images["TRIVY_IMAGE"]
        base = [
            "docker",
            "run",
            "--rm",
            "-v",
            "echosim-trivy-cache:/root/.cache/trivy",
            "-v",
            f"{OUT}:/reports",
        ]
        execute(
            [
                *base,
                "-v",
                f"{ROOT}:/repo:ro",
                image,
                "fs",
                "--scanners",
                "vuln",
                "--severity",
                "HIGH,CRITICAL",
                "--exit-code",
                "1",
                "--skip-dirs",
                "**/node_modules",
                "--skip-dirs",
                "**/.venv",
                "--skip-dirs",
                "artifacts",
                "--format",
                "json",
                "--output",
                "/reports/trivy-fs.json",
                "/repo",
            ]
        )
        refs = json.loads((OUT / "runtime-images.json").read_text())
        decisions = {}
        for name, ref in refs.items():
            report_path = OUT / f"trivy-{name}.json"
            report_path.unlink(missing_ok=True)
            execute(
                [
                    *base,
                    "-v",
                    "/var/run/docker.sock:/var/run/docker.sock",
                    image,
                    "image",
                    "--scanners",
                    "vuln",
                    "--severity",
                    "HIGH,CRITICAL",
                    "--exit-code",
                    "0",
                    "--format",
                    "json",
                    "--output",
                    f"/reports/trivy-{name}.json",
                    ref,
                ]
            )
            # Raw reports retain every CVE; scanner errors never reach this policy.
            decisions[name] = classify(ROOT, name, json.loads(report_path.read_text()))
        (OUT / "vulnerability-decisions.json").write_text(json.dumps(decisions, indent=2))
        blocked = sum(len(value["blocked"]) for value in decisions.values())
        exceptions = sum(len(value["approved_exceptions"]) for value in decisions.values())
        print(f"Vulnerability gate: {blocked} blocked, {exceptions} approved EX-005 records.")
        if blocked:
            raise SystemExit(1)
    else:
        raise SystemExit("unknown security check")


if __name__ == "__main__":
    main()
