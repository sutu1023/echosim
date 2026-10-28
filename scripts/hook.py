import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
staged = (
    subprocess.check_output(["git", "diff", "--cached", "--name-only", "-z"], cwd=root)
    .decode()
    .split("\0")
)
for name in staged:
    if name and (
        Path(name).name == ".env"
        or (Path(name).name.startswith(".env.") and Path(name).name != ".env.example")
    ):
        raise SystemExit("Do not commit local environment credentials")
image = next(
    line.split("=", 1)[1]
    for line in (root / "infra/images.env").read_text().splitlines()
    if line.startswith("GITLEAKS_IMAGE=")
)
subprocess.run(
    [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{root}:/repo:ro",
        image,
        "git",
        "/repo",
        "--staged",
        "--redact=100",
        "--no-banner",
    ],
    check=True,
)
