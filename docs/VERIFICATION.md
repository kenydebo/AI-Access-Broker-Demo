# Verified locally; live claims kept separate

## Completed

- **92 tests pass, no skips**, plus strict actual OPA policy checks. SDK MCP tests initialize and discover the real stdio server, issue/redeem through Unix sockets and assert zero downstream calls on deny. OAuth/executor tests use injected HTTP/tokens only.
- Actual installed `llama3.2:3b` → host → SDK MCP → disposable native-UID broker → actual OPA → mock Salesforce: A/A allow, A/B deny, B/A deny, B/B allow, plus adversarial B/A deny. Allowed cases read once and consume the grant; denied cases read zero times and issue no grant. Full safe evidence: [synthetic-model-evidence.json](synthetic-model-evidence.json).
- Actual in-app browser: A/A allowed; B/A denied with zero reads; B/B adversarial prompt caused the actual model to request A and was rejected by host scope check before MCP. Running button/selectors disabled; repeated-run conflict independently tested. Missing-model UI rendered generic failed-closed state, no data. Model summaries render as text, not HTML.
- [Browser proof: adversarial host denial](ui-denial.jpg). It is synthetic evidence, not Salesforce authentication or a live read.
- Identifying live org/user/email/fixture configuration preserved in ignored `.local/lab.json` and original local files/notes; public sources/tests/examples use fictitious identifiers. No Git repository/history exists in the project, so no Git-history scan was possible or needed here. A release allowlist plus known-identifier/path/credential-pattern inspection excludes private config, notes, caches, tools, venv, logs and sockets. This is not an exhaustive professional secret audit.

## Enterprise test evidence

The project author privately authenticated CustomerA/B and reported scripted split CustomerB B Id/Name allowed and A denied. Earlier runtime probes and app/fixture/user setup were supplied during the project. The automated model/MCP/OPA checks used synthetic identities and mock Salesforce.

## Still pending

Privately repeat the **model-driven live Salesforce** A/B path after configuration separation, then optionally the live UI. Exact commands are in [LIVE_SALESFORCE.md](LIVE_SALESFORCE.md). Live UI trusted attributes/OPA reasons are not exported by the current MCP boundary; only generic broker decision and safe host/grant/result traces are visible. Live UI has source/offline validation, not live integration proof.

Distinct agents under one OS UID, dynamic customer/ownership discovery, immediate human-revocation checks, production delegation/approval/writes, external deployment and production assurance are not implemented. Public publication has been requested for the specified repository; it has not yet been performed. License and independent provenance/public-release review remain decisions before publishing.
