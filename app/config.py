import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("LEDGER_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "ledger.db"
APP_PASSWORD = os.getenv("LEDGER_PASSWORD", "change-me-now")
SECRET_KEY = os.getenv("LEDGER_SECRET_KEY", "dev-only-change-this-secret")
ALERT_PERCENT = float(os.getenv("LEDGER_ALERT_PERCENT", "10"))
ALERT_DOLLARS = float(os.getenv("LEDGER_ALERT_DOLLARS", "5"))
