import os
import secrets
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
env = root / ".env"
if not env.exists():
    content = (
        "APP_ENV=development\nAPP_VERSION=0.0.1-dev\nMODEL_MODE=fake\n"
        f"POSTGRES_PASSWORD={secrets.token_hex(24)}\nWEB_PORT=18380\n"
    )
    fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(content)
    print("Created local .env with random credentials (values hidden).")
else:
    print("Existing .env preserved.")
subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=root, check=True)
print("Repository hook installed. Run make prepare, then make up.")
