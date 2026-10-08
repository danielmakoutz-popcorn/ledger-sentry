import sqlite3
from datetime import datetime
from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tx_date TEXT NOT NULL,
    description TEXT NOT NULL,
    merchant TEXT NOT NULL,
    amount REAL NOT NULL,
    category TEXT NOT NULL,
    account TEXT DEFAULT '',
    source TEXT DEFAULT 'csv',
    import_key TEXT UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(tx_date);
CREATE INDEX IF NOT EXISTS idx_transactions_merchant ON transactions(merchant);
CREATE INDEX IF NOT EXISTS idx_transactions_category ON transactions(category);
CREATE TABLE IF NOT EXISTS bank_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL UNIQUE,
    access_token_enc TEXT NOT NULL,
    cursor TEXT DEFAULT '',
    label TEXT DEFAULT 'Bank',
    last_sync TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with connect() as con:
        con.executescript(SCHEMA)

def insert_transaction(tx):
    with connect() as con:
        cur = con.execute(
            """INSERT OR IGNORE INTO transactions
            (tx_date, description, merchant, amount, category, account, source, import_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (tx['tx_date'], tx['description'], tx['merchant'], tx['amount'], tx['category'],
             tx.get('account',''), tx.get('source','csv'), tx['import_key'])
        )
        return cur.rowcount

def add_manual(tx_date, description, merchant, amount, category, account='Manual'):
    key = f"manual|{datetime.utcnow().isoformat()}|{tx_date}|{description}|{amount}"
    return insert_transaction({
        'tx_date': tx_date, 'description': description, 'merchant': merchant,
        'amount': amount, 'category': category, 'account': account,
        'source': 'manual', 'import_key': key
    })


def upsert_external_transaction(tx):
    with connect() as con:
        con.execute(
            """INSERT INTO transactions (tx_date,description,merchant,amount,category,account,source,import_key)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(import_key) DO UPDATE SET
              tx_date=excluded.tx_date, description=excluded.description, merchant=excluded.merchant,
              amount=excluded.amount, category=excluded.category, account=excluded.account, source=excluded.source""",
            (tx['tx_date'],tx['description'],tx['merchant'],tx['amount'],tx['category'],tx.get('account',''),tx.get('source','external'),tx['import_key'])
        )
