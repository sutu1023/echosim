"""Write an explicit local-only environment without overwriting existing settings."""

import os
import secrets
from pathlib import Path

path = Path(__file__).resolve().parents[1] / ".env"
content = (
    "APP_ENV=development\nAPP_VERSION=0.0.1\nPORT=8081\n"
    "HEALTH_TIMEOUT_SECONDS=3\nWORKER_POLL_SECONDS=10\n"
    "POSTGRES_DB=echosim\nPOSTGRES_USER=echosim\n"
    f"POSTGRES_PASSWORD={secrets.token_hex(32)}\n"
)
try:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    raise SystemExit(".env already exists; preserved without changes") from None
with os.fdopen(fd, "w") as stream:
    stream.write(content)
print("Created local .env with explicit settings and a generated database password")
