# Salesforce human authorizes; runtime executes

The enterprise demo uses two OAuth roles in broker memory:

- Human Login ECA: authorization code + S256 PKCE, confidential secret entered privately, scopes `api id`, callback `http://localhost:1717/OauthRedirect`.
- Separately approved runtime ECA: client credentials with the integration user's existing read permissions. The runtime must read both designated synthetic fixtures. Broker policy narrows each human task to A or B.

The Human Login app is configured. The two test-user passwords were created privately. Its callback acceptance and permissions were confirmed during setup. No additional Salesforce permission/app/record change is part of this package.

## Non-secret configuration

The identifying setup is preserved in **ignored `.local/lab.json`**. Original `human_users.json`, `fixtures.json` and private handoff notes are retained locally and excluded from the release archive. Public files contain fictitious IDs and `example.my.salesforce.com` only. Tests use the public example, never the live local config.

For another org, copy `examples/lab.example.json` into `.local/lab.json`, replace the fictitious org/host/human/runtime/record IDs and API version, and set `lab.mode` to `local`. This config is server-owned, trusted administration data. **Do not put tokens, consumer secrets or passwords in it.** Unknown credential members are rejected. Example mode is refused for live login. Keep `.local` out of Git, screenshots and releases. Register resource/customer scope carefully: a mislabeled fixture mapping is a misconfigured authority.

## Private A/B validation

1. Stop your previous broker yourself with Ctrl-C. Use a fresh socket if an old path remains; startup never auto-deletes an occupied socket.
2. Terminal 1:

```sh
.venv/bin/python -m broker_lab.user_login --approved-setup --executor runtime --socket .run/split.sock
```

Prompt order: Human Login consumer key → Human Login consumer secret → open printed URL in a fresh private browser and sign in → separate RUNTIME consumer key → separate RUNTIME consumer secret. Do not share any credential/code/token/authorization URL in chat or logs. The callback binds only `127.0.0.1:1717`, closes after login, and does not log query strings. No refresh token is requested or persisted.

After CustomerB login, expect `Executor: verified integration runtime. Human session authorizes only.`, trusted scope B, and the Unix socket-ready message. Keep this terminal running. Login alone performs identity checks but no Opportunity read.

3. Terminal 2:

```sh
.venv/bin/python -m broker_lab.mcp_client --socket .run/split.sock --record B
.venv/bin/python -m broker_lab.mcp_client --socket .run/split.sock --record A
```

Expect B's configured synthetic Id/Name, then A denial. Repeat with CustomerA after Ctrl-C, close all private windows, and use the next run's fresh URL; expect A allow/B deny. The authenticated returned subject determines the scope even if you intended another account.

4. Complete agent-driven integration, using the same privately authenticated split broker:

```sh
.venv/bin/python -m broker_lab.ollama_host --socket .run/split.sock --model llama3.2:3b --record B
.venv/bin/python -m broker_lab.ollama_host --socket .run/split.sock --model llama3.2:3b --record A
# Optional live UI in a third terminal:
.venv/bin/python -m broker_lab.web_demo --live-broker-socket .run/split.sock
```

Only the bounded Id/Name fixture result reaches your local model/UI. The broker keeps both Salesforce tokens; the runtime token executes reads. The UI does not authenticate, handle credentials, display live trusted attributes or export detailed live OPA reasons. Generic live denial includes policy, expired session/grant or unavailable broker. Synthetic onboarding remains a distinct mode.

`--approved-setup` is an operator acknowledgement, not authentication. The legacy default `--executor human` uses the human token for execution and prints that limitation; use explicit runtime mode for this architecture.

## Identity verification and limits

Human OAuth verifies state, PKCE, pinned TLS/no redirects/proxies, returned instance/org/registered subject, documented HMAC over `id + issued_at`, and a fixed-host authenticated Identity Service response for the same org/user/active state. The access token is opaque, not assumed JWT. No ID token/OIDC or introspection verification is claimed. Runtime OAuth checks its exact configured org/user/instance independently.

Human active is observed at login, not continuously checked. Local identity/session expires after 15 minutes; issue, redemption and downstream read check expiry. Salesforce runtime-token rejection fails closed after consuming the grant. Ctrl-C discards tokens locally; it does not revoke Salesforce OAuth grants. Python cannot promise forensic memory erasure. Same OS UID can use the session; distinct-agent authentication, immediate human-revocation detection and production delegation are not implemented.

[Salesforce OAuth web-server flow](https://help.salesforce.com/s/articleView?id=remoteaccess_oauth_web_server_flow.htm&language=en_US&type=5), [ECA settings](https://help.salesforce.com/s/articleView?id=xcloud.configure_external_client_app_oauth_settings.htm&language=en_US&type=5), [scope definitions](https://help.salesforce.com/s/articleView?id=remoteaccess_oauth_scopes.htm&language=en_US&type=5), [documented CLI loopback callback](https://developer.salesforce.com/docs/platform/sfdx-dev/guide/sfdx-dev-auth-connected-app.html).
