# Release checks

Completion checklist:

- [x] Preserve the local live non-secret configuration locally; credentials remain private memory entries.
- [x] Fictitious public configuration and config-driven Rego/Salesforce adapters; no embedded real org/user/email/fixture identifiers in release files.
- [x] Real synthetic model/MCP/OPA A/B and adversarial runs, safe evidence and browser-tested local UI.
- [x] Offline tests, quickstart, Salesforce-centered runbook, trust boundaries and limitations.
- [x] Explicit release file allowlist, content inspection, archive/hash/manifest and clean-extraction test.
- [ ] Private model/live-UI Salesforce validation after these changes.
- [ ] Select/approve a repository license and public identity/README attribution. No license was invented without a license choice.
- [ ] Review dependency/OPA provenance and disclosures before public distribution.
- [ ] Publish to the requested destination `kenydebo/AI-Access-Broker` after repository/account checks and the release gate. Publication and the targeted initial-history replacement are authorized. The selected account is verified. Public visibility remains gated on the old commit no longer being retrievable.

Run `scripts/package_release.py` to prepare an archive locally. It includes only specified source, tests, schemas, fictitious examples, web assets, public docs, dependency review records and scripts. It excludes `.local`, original identifying fixtures/human config, private historical handoffs, caches, installed tools/models/venv, sockets, OS files, logs and credentials. No user files or history are deleted. The working directory remains private; share only a reviewed archive, not the entire folder.

The scan checks known private identifiers and personal paths plus obvious credential-file/key patterns. Test credentials are explicitly fictitious. Passing does not guarantee that a future edit is safe: rerun review/package before every release. The archive contains no license grant yet and is a **review candidate**, not a published open-source release. No hosted walkthrough or external server is included. The destination repository will be inspected before publication; existing content and licenses will be preserved.
