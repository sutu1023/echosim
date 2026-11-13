# Design

## Context

See proposal.md. Existing app.py exposes health/static routes; main is the composition root. App.tsx contains only health UI; Vite proxies only /health. httpx is currently a dev dependency. There are no accounts, business tables or model adapters. The eight engineering standards apply.

## Goals / Non-Goals

**Goals:** Isolate a local diagnostic from story state and prove a real provider round trip with ephemeral credentials.

**Non-Goals:** No background job, database transaction, persistence, user configuration, creative evaluation or public deployment.

## Decisions

- Add a separate /dev/model-test page and POST /api/v1/model-connection-tests. Keep / and /health unchanged. Register diagnostic routes only with development plus explicit MODEL_CONNECTION_TEST_ENABLED=true; never allow the flag to override production. Tests instantiate development settings with a fake adapter. Document loopback port bindings and enforce same-origin browser requests; do not use forwarded headers to trust an arbitrary origin. Reject unknown fields. This is a development tool, not an exception granting access to story resources.
- API validates {api_key}; service controls the fixed test and result classification; domain defines provider contracts; adapter makes the external request; main injects dependencies. No adapters/framework imports in services/domain. Promote httpx to locked runtime dependency for cancellable async HTTPS rather than a full model SDK; retain public-address validation at the actual connection and disable redirects/environment proxy inheritance. Do not rely on an unbound DNS precheck that permits re-resolution bypass.
- Fixed endpoint: https://opencode.ai/inference/go/openai/v1/chat/completions; Bearer authentication; model deepseek-v4.1-flash; stream=false; max_tokens=256; omit temperature and record provider-default. Prompt v1: “请仅回复：连接测试成功。” Accept any nonempty final assistant text, not exact wording. Do not display or log raw model content. Version the configuration as opencode-go-test-v1. Official reference: https://models.opencode.ai/providers/opencode-go/ (checked 2026-11-13); actual account access remains unverified.
- Use one 60-second overall cancellation deadline including DNS/connect/read, not independent per-chunk timeouts. No automatic retry, including rate limit and timeout. Bound response size to 1 MiB and classify overflow as invalid response. A truncated response with no final text is not success; verify the output budget during the real test rather than silently changing parameters.
- Success JSON contains status, provider, model, duration_ms, usage or null, request_id. Validate usage as nonnegative integers; unavailable/invalid usage does not invalidate an otherwise valid answer. Error mappings are specified in R4. Provider 401 maps to a provider error, not an application login challenge.
- React uses a password input with autocomplete disabled; no browser persistence, request-body logging or raw error rendering. Clear component state after every outcome and on unmount. Backend releases request-scoped references; this is not a promise of physical memory zeroization. Add same-origin /api proxy for Vite and a bounded frontend wait slightly longer than backend deadline.
- Maintain standard structured logging with request correlation and safe diagnostic metadata only. Add marker-based leakage tests for successful, rejected and malformed requests. Empty or malformed credentials must not appear in validation errors.
- The 10–20 business AI evaluations in ai.md apply when implementing creative model behavior. This diagnostic has no materials, character visibility, tools or canon changes; record those evaluations as not applicable here, not passed. Fixed prompt response validation and security/failure tests remain mandatory. No new glossary term or irreversible architectural trade-off is introduced, so no glossary or ADR addition is warranted.

## Risks / Trade-offs

- Local diagnostic lacks authentication → loopback-only launch instructions, explicit enablement, same-origin protections and production 404; not suitable for shared/public hosting.
- Subscription limits or provider changes → distinguish real evidence from mocked checks, record the model/config versions and do not infer paid access from documentation.
- Reasoning consumes output budget → classify missing final answer accurately; any parameter adjustment must update the version and rerun verification.
- KEY exists transiently in process memory → minimize lifetime and test observable leakage; never claim secure memory erasure.

## Migration Plan

No data migration. Before implementation, update product/standards descriptions that currently say only the runtime exists to distinguish the planned local diagnostic from P13. Add the optional enable flag without making existing runtime configuration invalid. Default deployment remains disabled. Roll back by disabling the flag or restoring the previous build; no stored credentials require cleanup.

Run make check, make audit for dependency changes, and make smoke for runtime regression. Add mocked normal/failure/boundary checks and a real local browser test where the user enters the KEY. Store exact evidence and pending external checks in docs/verification/2026-11-13-model-connection-test.md and update traceability. Public deployment, SSE and backup restoration remain unverified; M0 remains incomplete.

## Provider protocol correction

OpenCode Go requires a client-specific User-Agent and x-opencode-session (official docs: https://opencode.ai/docs/console/go/#where-can-i-use-it). Each standalone one-request diagnostic is its own conversation and receives a fresh random UUID; no user identifier or KEY is used to derive it. Identify honestly as EchoSim-Connection-Test/0.0.1. Do not impersonate a validated coding client. Go is primarily intended for coding agents; future creative use requires a separate provider suitability decision.
