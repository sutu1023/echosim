---
name: verify-change
description: Implement and verify an EchoSim behavior change with traceable checks.
---

1. Read AGENTS.md, applicable standards, and the active change's specs and tasks.
2. Map each changed Scenario to normal, failure, and boundary cases; record the mapping in docs/verification/traceability.md.
3. Add tests that fail when the required behavior is broken; inject external dependencies for offline tests.
4. Run make check; for runtime/deployment changes also run make smoke. Fix failures without relaxing requirements.
5. Record commands, actual outcomes and limitations in docs/verification/<change>.md; update only completed task boxes.
