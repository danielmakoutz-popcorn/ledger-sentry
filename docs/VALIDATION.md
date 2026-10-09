# Ledger Sentry — validation evidence

Recorded **2026-10-09 UTC / 2026-10-08 Pacific**. Application source inspected at [`89dfb5e`](https://github.com/danielmakoutz-popcorn/ledger-sentry/commit/89dfb5e0d14d5c6179f9c946ff3bf2d147e099cc). The validator was added with this documentation update; application code was unchanged.

## Executed: offline data behavior

Environment: Python **3.12.14** and its standard library. No bank account, credentials, network requests, or household database were used. The checks run in a temporary directory that is removed on completion.

From the repository root:

```bash
python3 scripts/validate_local.py
```

Actual result: exit code **0**.

```text
PASS source syntax: 6 Python files
PASS CSV first import: 15 added, 0 skipped, 0 errors
PASS CSV repeat import: 0 added, 15 skipped, 0 errors
PASS duplicate imports preserve a 15-row database
PASS sample recurring alerts: Netflix +5.00, Cox +15.00
PASS one malformed row is reported while the valid row is imported
PASS external update replaces one stable-id row instead of duplicating it
PASS SQLite backup opens with all 17 synthetic rows
COMPLETE offline data checks; HTTP, Docker, and Plaid were not exercised
```

This demonstrates import idempotence **for the same account label**, per-row error isolation, deterministic alerts, update behavior, and a readable SQLite backup. It does not demonstrate an automated backup feature, correct handling of every bank CSV format, or end-to-end service recovery. Two otherwise identical CSV rows collapse to one import key; this is a known trade-off of the current deduplication design.

## Inspected: source and deployment configuration

| Claim | What is present | Validation level |
| --- | --- | --- |
| Authenticated UI | Signed session middleware and route guards | Source inspection; no HTTP session test |
| Token encryption | Fernet encrypt/decrypt functions, key derived from the session secret | Source inspection; no token round-trip or key-rotation test |
| Automatic sync | In-process task, sync cursor, reconciliation and stored last error | Source inspection; no Plaid requests |
| Persistent local service | `/data` mount, localhost port binding, `unless-stopped` | Configuration inspection; no container run |
| Health endpoint | Fixed `{"ok": true}` response | Source inspection; liveness only |

No GitHub Actions workflow or run was present at inspection. A passing offline transcript is not a CI result.

## Local operation and recovery

The following is a **runbook to execute on the local deployment**, not a record of commands run during validation.

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8787/health
docker compose logs --tail=100 ledger-sentry
```

Expected liveness response, **not captured here**: `{"ok":true}`. Also log in, import the sample into a test account, and inspect `/api/summary`; a health response alone is insufficient.

For a consistent manual backup, stop writes with `docker compose stop ledger-sentry`, copy `data/ledger.db` to an encrypted destination, then run `docker compose start ledger-sentry`. Preserve the original secret separately and securely. To test recovery, restore into an isolated test deployment with that secret, verify representative rows and login, and record the results before relying on the procedure.

Useful first-line checks: verify the localhost port, container logs, writable data directory and disk capacity, and the effective Compose environment. If Plaid remains disabled, check that the optional variables are explicitly passed to the service; Compose `.env` interpolation does not automatically inject every variable into a container.

## Pending evidence

- Fresh Docker build/start and HTTP authentication checks.
- Plaid sandbox linking, reconciliation, failure reporting, and secret-rotation behavior.
- A dated recovery exercise on an isolated deployment and automated CI execution.

These are validation gaps and suggested next improvements, not completed work or delivery commitments.
