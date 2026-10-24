import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))
IMG_DIR = os.path.join(DATA_DIR, "images")
os.makedirs(IMG_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "app.db")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


@contextmanager
def db():
    c = conn()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  description TEXT DEFAULT '',
  benefits TEXT DEFAULT '',
  price TEXT DEFAULT '',
  audience TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  images TEXT DEFAULT '[]',
  active INTEGER DEFAULT 1,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  enabled INTEGER DEFAULT 0,
  interval_seconds INTEGER DEFAULT 600,
  products_per_run INTEGER DEFAULT 1,
  product_ids TEXT DEFAULT '[]',
  prompts_per_product INTEGER DEFAULT 3,
  seconds_each INTEGER DEFAULT 7,
  mode TEXT DEFAULT 'separate',
  language TEXT DEFAULT 'en',
  target_tool TEXT DEFAULT '',
  style TEXT DEFAULT '',
  extra TEXT DEFAULT '',
  max_backlog INTEGER DEFAULT 30,
  webhook_url TEXT DEFAULT '',
  webhook_auth TEXT DEFAULT '',
  last_run TEXT
);
CREATE TABLE IF NOT EXISTS prompts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  job_id INTEGER,
  batch_id TEXT,
  idx INTEGER DEFAULT 1,
  seconds INTEGER DEFAULT 7,
  mode TEXT DEFAULT 'separate',
  text TEXT NOT NULL,
  status TEXT DEFAULT 'new',
  created_at TEXT,
  used_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_prompts_status ON prompts(status, product_id, job_id);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER,
  at TEXT,
  ok INTEGER,
  count INTEGER DEFAULT 0,
  message TEXT DEFAULT ''
);
"""


def init():
    with db() as c:
        c.executescript(SCHEMA)
