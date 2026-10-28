"""Validate the frontmatter of all repo-owned and generated Codex skills."""

from pathlib import Path

import yaml

root = Path(__file__).resolve().parents[1]
paths = sorted((root / ".agents/skills").glob("*/SKILL.md"))
assert paths, "repository skills missing"
for path in paths:
    text = path.read_text()
    assert text.startswith("---\n"), f"missing frontmatter: {path}"
    data = yaml.safe_load(text.split("---", 2)[1])
    assert data["name"] == path.parent.name, f"wrong skill name: {path}"
    assert isinstance(data["description"], str) and data["description"].strip()
print(f"Validated {len(paths)} repository skills")
