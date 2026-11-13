"""Offline structural checks; run from the repository root."""

import ast
import re
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
errors = []
for layer in ("domain", "services"):
    for path in (root / "backend/src/echosim" / layer).glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
                if node.level:
                    errors.append(
                        f"{path}: use absolute imports for dependency verification"
                    )
            for module in modules:
                denied = ("fastapi", "psycopg", "echosim.api", "echosim.adapters")
                if layer == "domain":
                    denied += ("echosim.services",)
                if module.startswith(denied):
                    errors.append(f"{path}: forbidden dependency {module}")
for key in (
    "product",
    "backend",
    "security",
    "testing",
    "operations",
    "ai",
    "data",
    "delivery",
):
    if not (root / f"docs/standards/{key}.md").is_file():
        errors.append(f"Missing standard: {key}")
for line in (root / ".env.example").read_text().splitlines():
    if line.strip() and not re.fullmatch(r"[A-Z_]+=", line):
        errors.append("Example environment must contain keys only")
tracked = (
    subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
)
for name in filter(None, tracked):
    path = Path(name)
    if path.name.startswith(".env") and path.name != ".env.example":
        errors.append(f"Local environment tracked: {name}")
    if (
        path.suffix in {".pem", ".key"}
        or ".venv" in path.parts
        or "node_modules" in path.parts
    ):
        errors.append(f"Private/generated file tracked: {name}")
    p = root / path
    if p.is_file() and p.stat().st_size < 1_000_000:
        text = p.read_text(errors="replace")
        if re.search(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----", text):
            errors.append(f"Private key material: {name}")
        if re.search(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9]{32,})", text):
            errors.append(f"Possible API token: {name}")
if errors:
    raise SystemExit("\n".join(errors))
print("Repository structure and tracked-file checks passed")
