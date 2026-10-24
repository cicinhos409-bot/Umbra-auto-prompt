"""Acesso ao Postgres (Neon na Vercel, ou qualquer Postgres via DATABASE_URL)."""
import os
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row


def database_url() -> str:
    url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or ""
    if not url:
        raise RuntimeError("DATABASE_URL não configurada (conecte um Postgres/Neon ao projeto)")
    return url


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Conn:
    """Pequeno adaptador: aceita placeholders '?' e devolve linhas como dict."""

    def __init__(self, raw: psycopg.Connection):
        self.raw = raw

    def execute(self, sql: str, params=()):
        return self.raw.execute(sql.replace("?", "%s"), tuple(params))

    def insert(self, sql: str, params=()) -> int:
        return self.raw.execute(sql.replace("?", "%s") + " RETURNING id", tuple(params)).fetchone()["id"]


def conn() -> Conn:
    # prepare_threshold=None: compatível com PgBouncer / pooler do Neon
    raw = psycopg.connect(database_url(), row_factory=dict_row, prepare_threshold=None, connect_timeout=15)
    return Conn(raw)


@contextmanager
def db():
    c = conn()
    try:
        yield c
        c.raw.commit()
    except Exception:
        c.raw.rollback()
        raise
    finally:
        c.raw.close()


SCHEMA = [
    """CREATE TABLE IF NOT EXISTS products (
      id SERIAL PRIMARY KEY,
      name TEXT NOT NULL,
      description TEXT DEFAULT '',
      benefits TEXT DEFAULT '',
      price TEXT DEFAULT '',
      audience TEXT DEFAULT '',
      notes TEXT DEFAULT '',
      images TEXT DEFAULT '[]',
      active INTEGER DEFAULT 1,
      created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS images (
      id TEXT PRIMARY KEY,
      product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
      content_type TEXT NOT NULL,
      data BYTEA NOT NULL,
      created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS jobs (
      id SERIAL PRIMARY KEY,
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
      last_run TEXT)""",
    """CREATE TABLE IF NOT EXISTS prompts (
      id SERIAL PRIMARY KEY,
      product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
      job_id INTEGER,
      batch_id TEXT,
      idx INTEGER DEFAULT 1,
      seconds INTEGER DEFAULT 7,
      mode TEXT DEFAULT 'separate',
      text TEXT NOT NULL,
      status TEXT DEFAULT 'new',
      created_at TEXT,
      used_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_prompts_status ON prompts(status, product_id, job_id)",
    "CREATE INDEX IF NOT EXISTS ix_prompts_product ON prompts(product_id, id)",
    """CREATE TABLE IF NOT EXISTS runs (
      id SERIAL PRIMARY KEY,
      job_id INTEGER,
      at TEXT,
      ok INTEGER,
      count INTEGER DEFAULT 0,
      message TEXT DEFAULT '')""",
]


def init():
    """Cria as tabelas (idempotente, com lock para cold starts simultâneos)."""
    with db() as c:
        c.execute("SELECT pg_advisory_xact_lock(7001)")
        for stmt in SCHEMA:
            c.execute(stmt)
