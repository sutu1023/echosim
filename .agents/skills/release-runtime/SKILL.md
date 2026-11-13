---
name: release-runtime
description: Verify and release a versioned EchoSim runtime baseline.
---

1. Read operations and delivery standards, active specs, and verification evidence.
2. Run make check and make smoke. Inspect locked dependencies and repository history for secrets.
3. Confirm GitHub check passed for the intended commit and main protection remains enabled.
4. Archive completed change and validate main specs. Merge through the protected branch workflow with zero required approvals.
5. Tag only the verified merged commit. Record rollback target and deployment limitations.
6. A repository release is not a cloud deployment. Deploy only within explicit authorization and verify readiness before routing traffic.
