"""App-owned JSON logs: seven UTC calendar days and bounded disk usage."""

import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

_NAME = re.compile(r"\d{4}-\d{2}-\d{2}(?:\.[a-f0-9]+)?\.jsonl\Z")


def prune_logs(directory: Path, now: datetime, max_bytes: int = 50 * 1024**2):
    cutoff = (now.date() - timedelta(days=6)).isoformat()
    files = []
    for path in directory.glob("*.jsonl"):
        if not _NAME.fullmatch(path.name) or path.is_symlink():
            continue
        if path.name[:10] < cutoff:
            path.unlink()
        else:
            files.append(path)
    total = sum(path.stat().st_size for path in files)
    for path in sorted(files, key=lambda p: p.stat().st_mtime):
        if total <= max_bytes:
            break
        total -= path.stat().st_size
        path.unlink()


class DailyJSONHandler(logging.Handler):
    def __init__(self, directory: Path):
        super().__init__()
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        prune_logs(directory, datetime.now(UTC))

    def emit(self, record):
        now = datetime.now(UTC)
        prune_logs(self.directory, now)
        path = self.directory / f"{now.date().isoformat()}.jsonl"
        if path.exists() and path.stat().st_size >= 10 * 1024**2:
            path.rename(path.with_name(f"{now.date().isoformat()}.{uuid4().hex}.jsonl"))
        with path.open("a", encoding="utf-8") as output:
            output.write(self.format(record) + "\n")
