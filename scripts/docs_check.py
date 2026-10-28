import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "product": ("P", 10),
    "backend": ("B", 12),
    "security": ("S", 14),
    "testing": ("T", 12),
    "ops": ("O", 11),
    "ai": ("A", 10),
    "data": ("D", 9),
    "review": ("R", 8),
}


def check(root: Path) -> list[str]:
    errors = []
    for name, (prefix, count) in EXPECTED.items():
        path = root / "docs/standards" / f"{name}.md"
        if not path.exists():
            errors.append(f"missing standard: {name}")
            continue
        blocks = re.split(r"^## ", path.read_text(), flags=re.MULTILINE)[1:]
        actual = [block.split()[0] for block in blocks]
        if actual != [f"{prefix}{i:02}" for i in range(1, count + 1)]:
            errors.append(f"wrong rule IDs: {name}")
        for block in blocks:
            if not all(
                label in block
                for label in [
                    "- 规则：",
                    "- 检查：",
                    "- 违反时：",
                    "- 启用阶段：",
                    "- 例外：",
                ]
            ):
                errors.append(f"incomplete rule: {name}/{block.split()[0]}")
    policy = json.loads((root / "config/policy.json").read_text())
    if policy["model"]["thinking"] or policy["capacity"]["global_runs"] != 2:
        errors.append("policy contradicts approved baseline")
    sources = []
    for pattern in [
        "api/tests/test_*.py",
        "web/src/*.test.tsx",
        "web/e2e/*.ts",
        "scripts/run.py",
        "scripts/security.py",
    ]:
        sources.extend(root.glob(pattern))
    mapping = {}
    for spec in (root / "openspec").rglob("spec.md"):
        for sid in re.findall(r"#### Scenario: (SC-[A-Z]+-\d+)", spec.read_text()):
            # After archive the same scenario is in both baseline and archived delta.
            mapping.setdefault(sid, [])
            for source in sources:
                content = source.read_text()
                if sid in content or sid.replace("-", "_") in content:
                    label = str(source.relative_to(root))
                    if label not in mapping[sid]:
                        mapping[sid].append(label)
    for sid, targets in mapping.items():
        if not targets:
            errors.append(f"scenario without verification: {sid}")
    output = root / "artifacts"
    output.mkdir(exist_ok=True)
    (output / "traceability.json").write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2) + "\n"
    )
    return errors


if __name__ == "__main__":
    problems = check(ROOT)
    if problems:
        raise SystemExit("\n".join(problems))
    print("86 rules complete; scenario verification matrix: artifacts/traceability.json")
