"""Only explicitly isolated notes are eligible for reduced gates; everything else is full."""

import json
import os
import subprocess
from pathlib import Path

full = True
event_path = os.environ.get("GITHUB_EVENT_PATH")
if os.environ.get("GITHUB_EVENT_NAME") == "pull_request" and event_path:
    event = json.loads(Path(event_path).read_text())
    base = event["pull_request"]["base"]["sha"]
    names = subprocess.check_output(
        ["git", "diff", "--name-only", base, "HEAD"], text=True
    ).splitlines()
    # README, specs, standards, skills, prompts and operation instructions ALWAYS run full.
    full = not names or not all(
        name.startswith("docs/notes/") and name.endswith(".md") for name in names
    )
print("full=" + str(full).lower())
