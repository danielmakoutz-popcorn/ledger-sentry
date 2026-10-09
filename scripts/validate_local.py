#!/usr/bin/env python3
"""Check CSV and SQLite behavior with synthetic data in a temporary directory."""
import ast
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check(condition, label):
    if not condition:
        raise RuntimeError(label)
    print(f"PASS {label}")


def main():
    files = sorted((ROOT / "app").rglob("*.py"))
    for path in files:
        ast.parse(path.read_text(), filename=str(path.relative_to(ROOT)))
    print(f"PASS source syntax: {len(files)} Python files")

    with TemporaryDirectory(prefix="ledger-validation-") as temporary:
        os.environ["LEDGER_DATA_DIR"] = temporary
        os.environ["LEDGER_ALERT_PERCENT"] = "10"
        os.environ["LEDGER_ALERT_DOLLARS"] = "5"
        from app.config import DB_PATH
        from app.db import connect, init_db, upsert_external_transaction
        from app.logic import dashboard_data, import_csv

        init_db()
        fixture = (ROOT / "sample-transactions.csv").read_bytes()
        check(import_csv(fixture, "Synthetic sample", "negative_is_spend") == (15, 0, []),
              "CSV first import: 15 added, 0 skipped, 0 errors")
        check(import_csv(fixture, "Synthetic sample", "negative_is_spend") == (0, 15, []),
              "CSV repeat import: 0 added, 15 skipped, 0 errors")
        with closing(connect()) as connection:
            count = connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        check(count == 15, "duplicate imports preserve a 15-row database")

        alerts = {a["merchant"]: round(a["delta"], 2) for a in dashboard_data()["alerts"]}
        check(alerts.get("Netflix.Com") == 5.0 and alerts.get("Cox Communications") == 15.0,
              "sample recurring alerts: Netflix +5.00, Cox +15.00")

        mixed = b"Date,Description,Amount\nnot-a-date,Synthetic invalid,-2\n2026-01-01,Synthetic valid,-3\n"
        added, skipped, errors = import_csv(mixed, "Synthetic malformed", "negative_is_spend")
        check((added, skipped, len(errors)) == (1, 0, 1),
              "one malformed row is reported while the valid row is imported")

        transaction = {"tx_date": "2026-01-02", "description": "Synthetic external",
                       "merchant": "Synthetic external", "amount": 10.0, "category": "Other",
                       "source": "synthetic", "import_key": "synthetic|stable-id"}
        upsert_external_transaction(transaction)
        upsert_external_transaction({**transaction, "amount": 11.0})
        with closing(connect()) as connection:
            rows = connection.execute("SELECT amount FROM transactions WHERE import_key=?",
                                      (transaction["import_key"],)).fetchall()
        check(len(rows) == 1 and rows[0]["amount"] == 11.0,
              "external update replaces one stable-id row instead of duplicating it")

        with closing(sqlite3.connect(DB_PATH)) as source, closing(sqlite3.connect(Path(temporary) / "backup.db")) as backup:
            source.backup(backup)
            original = source.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            restored = backup.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        check(original == restored == 17, "SQLite backup opens with all 17 synthetic rows")

    print("COMPLETE offline data checks; HTTP, Docker, and Plaid were not exercised")


if __name__ == "__main__":
    main()
