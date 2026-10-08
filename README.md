# Ledger Sentry

A small, local-first household finance dashboard designed to run beside a Bitcoin node without touching Bitcoin Core.

## What works now

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

The question box is deterministic in this release. It does not need an AI API and cannot hallucinate financial totals.

## Optional automatic bank sync with Plaid

CSV import works with no third-party account. For automatic syncing, put your own Plaid credentials in `.env` and restart the container. Start with `PLAID_ENV=sandbox`; production connections require your Plaid production setup/approval. Open **Banks** in Ledger Sentry and choose **Connect bank**.

The app creates a Plaid Link session, exchanges the temporary public token on the backend, encrypts the resulting access token at rest, and uses `/transactions/sync` to reconcile added, modified, and removed transactions. A lightweight in-process task checks linked Items every `LEDGER_SYNC_MINUTES` (default 240 minutes).

Ledger Sentry never asks you to type an online-banking username or password into this app. Those credentials, when required by an institution, are handled inside the Plaid Link flow.

For OAuth institutions, set `PLAID_REDIRECT_URI` to the registered HTTPS Ledger Sentry URL required by your Plaid configuration.

## Backups

The persistent database is `data/ledger.db`. Back that file up only to an encrypted destination.

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
