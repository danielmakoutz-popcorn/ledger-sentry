# Ledger Sentry

A local-first household finance service built with **FastAPI, SQLite, and Docker Compose**. It imports transactions, identifies recurring charges, and exposes a password-protected dashboard and JSON summary. It can run beside a Bitcoin node without accessing Bitcoin Core.

The operations work is visible in persistent storage, repeatable imports, an optional background sync worker, a localhost-only service binding, and a container restart policy.

**Review the evidence:** [recorded validation and recovery steps](docs/VALIDATION.md). Offline checks passed for duplicate imports, malformed-row handling, recurring-charge alerts, external-record updates, and SQLite backup readability. HTTP, Docker startup, and Plaid integration were not exercised in that validation.

## Implemented in source

- Password-protected web UI
- CSV transaction imports with duplicate protection
- Optional Plaid bank linking and periodic automatic transaction sync
- Automatic merchant cleanup and spending categories
- Monthly category totals
- Recurring charge detection (weekly, monthly, annual)
- Alerts when recurring charges jump by a percentage or dollar threshold
- Cull candidates ranked by annualized discretionary cost
- Cancellation instructions for common services + official-help fallback search
- Deterministic question box: food totals, price increases, cull candidates, cancellation help
- Manual transaction entry
- JSON summary endpoint at `/api/summary`
- SQLite storage; no transaction data is sent to an LLM
- Plaid access tokens are encrypted at rest when bank sync is enabled

## Architecture and engineering evidence

| Component | Responsibility | Source / evidence |
| --- | --- | --- |
| FastAPI application | Session checks, HTML routes, CSV upload, JSON summary, `/health` | [app/main.py](app/main.py) |
| SQLite store | Unique import keys, parameterized writes, indexes, external-record upserts | [app/db.py](app/db.py); offline checks passed |
| Transaction logic | Normalize merchants, categorize spending, detect recurring charges and increases | [app/logic.py](app/logic.py); sample alerts verified |
| Plaid integration | Encrypt stored tokens, reconcile added/modified/removed records, retain sync cursor and last error | [app/plaid.py](app/plaid.py); source inspection only |
| Local deployment | Persist `/data`, bind `127.0.0.1:8787`, restart unless explicitly stopped | [Dockerfile](Dockerfile), [docker-compose.yml](docker-compose.yml); configuration inspection only |

CSV data flows through the import logic into SQLite; the dashboard and summary query that same store. When configured, the in-process Plaid task also writes to SQLite. This is a single-process local service.

## Security model

Ledger Sentry is intentionally separate from Bitcoin Core. It does not need Bitcoin RPC, wallet files, SSH keys, `bitcoin.conf`, or node credentials.

The Docker configuration binds the app only to `127.0.0.1:8787` by default. Do not change this to a public bind unless you know exactly why you are doing it.

For remote household access, use a private overlay network such as Tailscale and reverse proxy the local service through Tailscale Serve. Do **not** use Tailscale Funnel for this app.

## Quick start with Docker

```bash
cd ledger-sentry
cp .env.example .env
```

Edit `.env` and set a strong password and random secret. A Linux command for the secret:

```bash
openssl rand -hex 32
```

Then:

```bash
docker compose up -d --build
```

On the node itself, browse to:

```text
http://127.0.0.1:8787
```

To inspect status:

```bash
docker compose ps
docker compose logs -f ledger-sentry
```

To stop:

```bash
docker compose down
```

## Private access for two people with Tailscale

After both household devices and the node are members of the same approved tailnet, keep Ledger Sentry bound to localhost and run on the node:

```bash
tailscale serve --bg localhost:8787
```

Tailscale will show the private HTTPS URL. Tailnet ACLs should restrict access to only the intended users/devices.

## Importing a CSV

Use **Import** in the web UI.

Typical supported headers include:

- Date / Transaction Date / Posted Date
- Description / Merchant / Name / Details / Memo
- Amount
- or separate Debit and Credit columns

Choose whether purchases are represented as negative or positive numbers. Internally Ledger Sentry stores money spent as positive and money received/refunded as negative.

A `sample-transactions.csv` file is included for testing. With the sample imported as “purchases are negative,” you should see Netflix and Cox price-increase alerts.

## Recurring detection

Recurring detection currently looks for repeated normalized merchants with weekly (5–10 day), monthly (20–40 day), or annual (330–400 day) gaps. It is deliberately understandable rather than magical. The more transaction history you import, the better it gets.

## Ask box examples

- `What went up?`
- `How much on food this month?`
- `What can I cull?`
- `How do I cancel Spotify?`

The question box is deterministic in this release. Its answers use local calculations and rules rather than an AI API.

## Optional automatic bank sync with Plaid

CSV import works with no third-party account. The Plaid integration is implemented, but **the checked-in Compose file forwards only the password, session secret, and alert thresholds**. Editing the Plaid fields in `.env` alone does not enable bank sync inside the container.

For a local Plaid trial, put your credentials in `.env` and add the following `environment` entries to the `ledger-sentry` service in your local Compose configuration:

```yaml
PLAID_ENV: ${PLAID_ENV:-sandbox}
PLAID_CLIENT_ID: ${PLAID_CLIENT_ID:-}
PLAID_SECRET: ${PLAID_SECRET:-}
PLAID_REDIRECT_URI: ${PLAID_REDIRECT_URI:-}
LEDGER_SYNC_MINUTES: ${LEDGER_SYNC_MINUTES:-240}
```

Recreate the service with `docker compose up -d --build`. Start with `PLAID_ENV=sandbox`; production connections require your Plaid production setup/approval. Open **Banks** in Ledger Sentry and choose **Connect bank**. This integration has not been validated in the recorded offline checks.

The app creates a Plaid Link session, exchanges the temporary public token on the backend, encrypts the resulting access token at rest, and uses `/transactions/sync` to reconcile added, modified, and removed transactions. A lightweight in-process task checks linked Items every `LEDGER_SYNC_MINUTES` (default 240 minutes).

Ledger Sentry never asks you to type an online-banking username or password into this app. Those credentials, when required by an institution, are handled inside the Plaid Link flow.

For OAuth institutions, set `PLAID_REDIRECT_URI` to the registered HTTPS Ledger Sentry URL required by your Plaid configuration.

## Backups

The persistent database is `data/ledger.db`. Use SQLite's backup API, or stop the service before copying the database, and store the backup in an encrypted destination. See the [recovery steps](docs/VALIDATION.md#local-operation-and-recovery).

Token encryption uses a key derived from `LEDGER_SECRET_KEY`; preserve that secret securely with the backup. Changing it invalidates sessions and prevents decryption of previously stored Plaid tokens. Transaction rows themselves are not encrypted by the application.

## Validation and next improvements

Run the included checks with Python 3.12, without Docker or bank credentials:

```bash
python3 scripts/validate_local.py
```

The script uses a temporary database and the bundled sample CSV. It does not read or change the household database.

Next improvements, **not implemented or validated in this update**: forward optional sync configuration directly in Compose, report worker/database readiness separately from `/health`, and add HTTP/session and Plaid sandbox integration checks. `/health` currently returns a fixed `{"ok": true}`; it does not prove database or bank-sync health.

## Project layout

```text
app/
  main.py          FastAPI routes
  db.py            SQLite persistence
  logic.py         categories, recurring detection, alerts, assistant
  templates/       server-rendered UI
  static/          CSS
Dockerfile
docker-compose.yml
sample-transactions.csv
```
