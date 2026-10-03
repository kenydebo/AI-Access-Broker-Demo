# Lab threat model and limits

## Trust

Trusted: local broker source/process, server-owned configuration/policy, OS peer credentials, installed OPA, fixed Salesforce TLS endpoints, verified human/runtime OAuth responses. The project directory and same-UID processes are inside this lab's trust boundary. This is not a sandbox against a malicious local account.

Untrusted: model output/tool arguments, requested record/action/fields/task, web request bodies, Salesforce record text/model summaries. Customer selectors in synthetic mode inject test assumptions, not identity. Real Salesforce user identity is established only in the broker's private OAuth flow.

## Controls exercised

- Exact bounded request schema, allowlisted fields/aliases/action/task/audience, no SOQL/write/identity override.
- Host pins model arguments to the operator-selected request. Broker applies independently configured human/task scope; a valid host request can still be denied.
- Default-deny OPA; outage, malformed decision and policy change fail closed. Rego reasons come from the actual decision in synthetic mode.
- Random opaque grants held server-side, 60-second TTL, caller/request/human-session/executor binding, policy recheck and atomic single use before downstream read. Downstream failures consume the grant.
- Human identity and runtime execution token kept separately in broker memory. MCP/model/UI never receive OAuth credentials or grant handles. Returned data is projected; exact Salesforce record ID is checked.
- Private Unix socket directory/permissions. Existing socket paths are never automatically deleted; Ctrl-C removes only the process's own socket inode.
- UI binds only IPv4 loopback; validates Host and Origin, requires an ephemeral same-origin request nonce, rejects cross-site requests/unknown routes/extra or duplicate members, applies CSP/no-store, renders untrusted content as text and serializes jobs. The nonce is a local request guard, not persistent credentials or human authentication.

## Material limitations

Shared UID is not distinct agent identity. Synthetic selection/active/runtime validation are injected assumptions. Static customer/record assignments are not dynamic ownership/Account discovery. In live mode the runtime has broader underlying Salesforce permissions; broker scope and correct configuration are essential. OPA uses alias A/B while the adapter resolves authoritative configured record IDs.

Human revocation/active state is not polled during the 15-minute session. No refresh-token storage, grant revocation endpoint, production multi-user workload authentication, delegation protocol, durable audit log, approval executor, write path, external ingress or deployment exists. The live UI is a user-run client of an already authenticated broker, not a web login application. Do not expose it outside loopback or put it behind an external proxy.

Model answers remain untrusted and may be inaccurate even after a valid read. Prompt refusal is not an access-control guarantee; host/broker enforce scope. A model can decline to call a tool; the app suppresses an unsupported answer. Local job/model time and call budgets are bounded, but there is no production DoS defense. Python does not guarantee secret memory erasure; abrupt termination can leave a stale socket.

The source includes an isolated nonexecuting approval-preview prototype/tests, not a live approval feature. No production assurance, full OAuth/RAR/OIDC compliance, or independent security audit is claimed. Dependency checksum/hash evidence is recorded; independent OPA attestation verification was not completed and is a release-review item.
