import hashlib
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

from restore_drill import drill
from runtime_checks import check_runtime

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"


def call(args, **kwargs):
    return subprocess.run(args, cwd=ROOT, check=True, **kwargs)


def image_env():
    return dict(
        line.split("=", 1)
        for line in (ROOT / "infra/images.env").read_text().splitlines()
        if line and not line.startswith("#")
    )


def fingerprint():
    names = (
        subprocess.check_output(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
        )
        .decode()
        .split("\0")
    )
    digest = hashlib.sha256()
    for name in sorted(set(names)):
        path = ROOT / name
        if name and path.is_file() and not path.is_symlink():
            digest.update(name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def tests(prepare=False):
    # SC-GATE-01 SC-GATE-06: every invocation has a dedicated project, DB and network.
    name = f"echosim-test-{os.getpid()}-{secrets.token_hex(3)}"
    env = {**os.environ, "TEST_DB_PASSWORD": secrets.token_hex(24)}
    compose = [
        "docker",
        "compose",
        "--env-file",
        "infra/images.env",
        "-f",
        "compose.test.yaml",
        "-p",
        name,
    ]

    def dc(*args, **kwargs):
        return call([*compose, *args], env=env, **kwargs)

    ARTIFACTS.mkdir(exist_ok=True)
    try:
        if prepare:
            before = fingerprint()
            dc("build")
            if fingerprint() != before:
                raise SystemExit("Source changed during image preparation; repeat make prepare.")
            (ARTIFACTS / "prepared.json").write_text(json.dumps({"source": before}))
            runtime = {
                "api": "echosim-m0-api-test-runtime:local",
                "web": "echosim-m0-web-test-runtime:local",
                "postgres": "echosim-m0-postgres:16.15",
            }
            (ARTIFACTS / "runtime-images.json").write_text(json.dumps(runtime))
            provenance = {
                "commit": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
                ).strip(),
                "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)),
                "source_sha256": before,
                "images": {
                    key: json.loads(
                        subprocess.check_output(
                            ["docker", "image", "inspect", ref, "--format", "{{json .Id}}"],
                            text=True,
                        )
                    )
                    for key, ref in runtime.items()
                },
            }
            (ARTIFACTS / "build-provenance.json").write_text(json.dumps(provenance, indent=2))
            return
        if (
            not (ARTIFACTS / "prepared.json").exists()
            or json.loads((ARTIFACTS / "prepared.json").read_text())["source"] != fingerprint()
        ):
            raise SystemExit("Prepared test images differ from source; run make prepare first.")
        dc("up", "-d", "--wait", "postgres", "api", "web")
        dc("run", "--rm", "--no-deps", "api-tests")
        dc("run", "--rm", "--no-deps", "node-tests")
        dc("run", "--rm", "--no-deps", "browser-tests")
        # SC-GATE-09: authenticated encryption, tamper rejection and isolated restore.
        drill(compose, env, ROOT, ARTIFACTS)
        # SC-GATE-07 SC-GATE-08: real image switch, proxy faults, process stop.
        check_runtime(dc, env, ROOT, ARTIFACTS)
    except BaseException:
        with (ARTIFACTS / "compose-failure.log").open("w") as output:
            subprocess.run(
                [*compose, "logs", "--no-color", "api", "web"],
                cwd=ROOT,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                check=False,
            )
        raise
    finally:
        dc("down", "--volumes", "--remove-orphans")


def main():
    action = sys.argv[1]
    if action == "prepare":
        for image in image_env().values():
            call(["docker", "pull", image])
        tests(prepare=True)
    elif action == "test":
        tests()
    elif action == "check":
        call([sys.executable, "scripts/docs_check.py"])
        tests(prepare=True)
        tests()
        call([sys.executable, "scripts/security.py", "secrets"])
        call([sys.executable, "scripts/licenses.py"])
        call([sys.executable, "scripts/security.py", "vulnerabilities"])
    elif action == "status":
        import urllib.request

        values = dict(
            line.split("=", 1)
            for line in (ROOT / ".env").read_text().splitlines()
            if "=" in line and not line.startswith("#")
        )
        port = int(values["WEB_PORT"])
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as response:
            print(json.dumps(json.load(response), ensure_ascii=False, indent=2))
    else:
        raise SystemExit("unknown action")


if __name__ == "__main__":
    main()
