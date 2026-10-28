import json
import subprocess
import time


def check_runtime(dc, env, root, artifacts):
    # Actual HTTP+PostgreSQL sampling, distinct from stubbed unit tests.
    program = """
import json,time,urllib.request
samples=[]
for n in range(31):
 start=time.perf_counter()
 with urllib.request.urlopen('http://api:8000/health',timeout=5) as r: d=json.load(r)
 assert d['code']=='OK' and d['data']['dependencies']['postgres']=='up'
 if n:samples.append((time.perf_counter()-start)*1000)
p95=sorted(samples)[28]
print(json.dumps({'p95_ms':p95,'samples':30,'warning':p95>500,'failed':p95>1000,
 'scope':'real isolated API HTTP + PostgreSQL, warmed, concurrency 1'}))
assert p95<=1000
"""
    result = dc(
        "run",
        "--rm",
        "--no-deps",
        "api-tests",
        "python",
        "-c",
        program,
        capture_output=True,
        text=True,
    )
    latency = json.loads(result.stdout.strip())
    (artifacts / "api-latency.json").write_text(json.dumps(latency, indent=2) + "\n")
    print(f"Real API p95={latency['p95_ms']:.2f}ms; warning={latency['warning']}")

    # SC-GATE-07: physically different image source, not only an APP_VERSION label.
    dockerfile = artifacts / "candidate.Dockerfile"
    dockerfile.write_text(
        "FROM echosim-m0-api-test-runtime:local\n"
        "RUN printf 'BUILD_ID = \"candidate-v2\"\\n' > app/build_info.py\n"
    )
    subprocess.run(
        [
            "docker",
            "build",
            "--pull=false",
            "--network=none",
            "-f",
            str(dockerfile),
            "-t",
            "echosim-m0-api-candidate:local",
            ".",
        ],
        cwd=root,
        check=True,
    )
    versions = []
    inspect = "import json,urllib.request;print(json.dumps(json.load(urllib.request.urlopen('http://127.0.0.1:8000/health'))['data']))"
    for image, version, build in [
        ("echosim-m0-api-candidate:local", "candidate-v2", "candidate-v2"),
        ("echosim-m0-api-test-runtime:local", "test-v1", "skeleton-source-v1"),
    ]:
        env["TEST_API_IMAGE"], env["TEST_APP_VERSION"] = image, version
        started = time.monotonic()
        dc("up", "-d", "--wait", "--no-build", "--no-deps", "--force-recreate", "api")
        result = dc("exec", "-T", "api", "python", "-c", inspect, capture_output=True, text=True)
        data = json.loads(result.stdout.strip())
        assert data["version"] == version and data["build_id"] == build
        image_id = subprocess.check_output(
            ["docker", "image", "inspect", image, "--format", "{{.Id}}"], text=True
        ).strip()
        versions.append(
            {
                "version": version,
                "build_id": build,
                "image_id": image_id,
                "seconds": round(time.monotonic() - started, 3),
            }
        )
    assert versions[0]["image_id"] != versions[1]["image_id"]

    # SC-GATE-08: process stop on real container; in-flight signal tested separately.
    started = time.monotonic()
    dc("stop", "-t", "35", "api")
    seconds = time.monotonic() - started
    assert seconds < 30
    container = dc("ps", "-a", "-q", "api", capture_output=True, text=True).stdout.strip()
    exit_code = subprocess.check_output(
        ["docker", "inspect", container, "--format", "{{.State.ExitCode}}"], text=True
    ).strip()
    assert exit_code in {"0", "143"}, "SIGKILL/other abnormal exit is not graceful"
    # The proxy's default error logger must not serialize raw requests on upstream failure.
    probe = """
import urllib.request,urllib.error
req=urllib.request.Request('http://web/health?key=proxy-leak-sentinel',
 headers={'Authorization':'proxy-header-sentinel','Cookie':'proxy-cookie-sentinel'})
try: urllib.request.urlopen(req,timeout=5);raise AssertionError('expected failure')
except urllib.error.HTTPError as e: assert e.code==502
"""
    dc("run", "--rm", "--no-deps", "api-tests", "python", "-c", probe)
    logs = dc("logs", "--no-color", "web", capture_output=True, text=True).stdout
    assert not any(
        value in logs
        for value in [
            "proxy-leak-sentinel",
            "proxy-header-sentinel",
            "proxy-cookie-sentinel",
            '"remote_ip"',
            '"client_ip"',
        ]
    )
    (artifacts / "runtime-verification.json").write_text(
        json.dumps(
            {
                "versions": versions,
                "shutdown_seconds": seconds,
                "exit_code": int(exit_code),
                "model_calls": 0,
                "proxy_error_request_redacted": True,
                "scenarios": ["SC-GATE-01", "SC-GATE-06", "SC-GATE-07", "SC-GATE-08"],
            },
            indent=2,
        )
        + "\n"
    )
