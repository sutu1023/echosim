"""Inventory actual Python/Node toolchain installs; unknown/unapproved licenses fail closed."""

import fnmatch
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_INVENTORY = r"""
import importlib.metadata as m,json
rows=[]
for d in m.distributions():
 rows.append({'name':d.metadata['Name'],'version':d.version,
 'license':d.metadata.get('License-Expression') or d.metadata.get('License','UNKNOWN'),
 'ecosystem':'python'})
print(json.dumps(rows))
"""
NODE_INVENTORY = r"""
const fs=require('fs'),p=require('path');let rows=[];
for(const entry of fs.readdirSync('/workspace/node_modules/.pnpm')){
 const base=p.join('/workspace/node_modules/.pnpm',entry,'node_modules');
 if(!fs.existsSync(base))continue;
 for(const name of fs.readdirSync(base)){
  const dirs=name.startsWith('@')?
   fs.readdirSync(p.join(base,name)).map(n=>p.join(base,name,n)):[p.join(base,name)];
  for(const dir of dirs){
   const f=p.join(dir,'package.json');if(!fs.existsSync(f))continue;
   const d=JSON.parse(fs.readFileSync(f));
   rows.push({name:d.name,version:d.version,
    license:typeof d.license==='string'?d.license:'UNKNOWN',ecosystem:'node'});
  }
 }
}
console.log(JSON.stringify(rows));
"""


def approved(expression, name, policy):
    # SPDX boolean precedence is significant. Unsupported syntax fails closed.
    tokens = re.findall(r"\(|\)|[A-Za-z0-9.+-]+", expression)
    if "".join(tokens) != re.sub(r"\s+", "", expression):
        return False
    position = 0

    def atom():
        nonlocal position
        if position >= len(tokens):
            raise ValueError("missing operand")
        token = tokens[position]
        position += 1
        if token == "(":
            value = either()
            if position >= len(tokens) or tokens[position] != ")":
                raise ValueError("unclosed expression")
            position += 1
            return value
        if token in {"AND", "OR", "WITH", ")"}:
            raise ValueError("unexpected operator")
        return token in policy["allowed"] or any(
            fnmatch.fnmatchcase(name, pattern)
            for pattern in policy["reviewed_exceptions"].get(token, [])
        )

    def both():
        nonlocal position
        value = atom()
        while position < len(tokens) and tokens[position] == "AND":
            position += 1
            right = atom()
            value = value and right
        return value

    def either():
        nonlocal position
        value = both()
        while position < len(tokens) and tokens[position] == "OR":
            position += 1
            right = both()
            value = value or right
        return value

    try:
        result = either()
        return position == len(tokens) and result
    except ValueError:
        return False


def main():
    policy = json.loads((ROOT / "config/license-policy.json").read_text())
    rows = []
    for image, command in [
        ("echosim-m0-api-test:local", ["python", "-c", PYTHON_INVENTORY]),
        ("echosim-m0-node-test:local", ["node", "-e", NODE_INVENTORY]),
    ]:
        output = subprocess.check_output(
            ["docker", "run", "--rm", "--network", "none", image, *command], text=True
        )
        rows.extend(json.loads(output))
    unique = {(r["ecosystem"], r["name"], r["version"]): r for r in rows}
    rows = sorted(unique.values(), key=lambda r: (r["ecosystem"], r["name"]))
    bad = [r for r in rows if not approved(r["license"], r["name"], policy)]
    out = ROOT / "artifacts"
    out.mkdir(exist_ok=True)
    (out / "licenses.json").write_text(
        json.dumps({"packages": rows, "unapproved": bad}, ensure_ascii=False, indent=2) + "\n"
    )
    for row in bad:
        print(f"UNAPPROVED {row['ecosystem']} {row['name']}@{row['version']}: {row['license']}")
    if bad:
        raise SystemExit(
            "License gate blocked; see docs/license-review.md. Human decision required by S13/R04."
        )
    print(f"License gate passed: {len(rows)} installed packages.")


if __name__ == "__main__":
    main()
