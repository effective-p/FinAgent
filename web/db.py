"""PostgreSQL 연결 풀 및 스키마 초기화."""
from __future__ import annotations

import os
from contextlib import contextmanager

import psycopg2
from psycopg2 import pool as pg_pool

_pool: pg_pool.ThreadedConnectionPool | None = None


def _dsn() -> str:
    return (
        f"host={os.environ.get('PG_HOST', '10.0.0.21')} "
        f"port={os.environ.get('PG_PORT', '5433')} "
        f"dbname={os.environ.get('PG_DBNAME', 'finagent')} "
        f"user={os.environ.get('PG_USER', 'postgres')} "
        f"password={os.environ.get('PG_PASSWORD', 'nvidia')}"
    )


def get_pool() -> pg_pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = pg_pool.ThreadedConnectionPool(minconn=1, maxconn=10, dsn=_dsn())
    return _pool


@contextmanager
def get_conn():
    p = get_pool()
    conn = p.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        p.putconn(conn)


_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS llm_configs (
    id         SERIAL PRIMARY KEY,
    user_id    INT REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    provider   TEXT NOT NULL,
    model      TEXT NOT NULL,
    api_key    TEXT NOT NULL DEFAULT '',
    base_url   TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS runs (
    id                TEXT PRIMARY KEY,
    user_id           INT REFERENCES users(id),
    symbol            TEXT NOT NULL,
    stock_name        TEXT NOT NULL,
    start_date        TEXT NOT NULL,
    end_date          TEXT NOT NULL,
    initial_cash      REAL NOT NULL,
    trader_preference TEXT NOT NULL,
    llm_config_id     INT REFERENCES llm_configs(id),
    status            TEXT NOT NULL DEFAULT 'running',
    result_json       TEXT,
    error_msg         TEXT,
    created_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS memory (
    id         TEXT PRIMARY KEY,
    run_id     TEXT NOT NULL,
    collection TEXT NOT NULL,
    document   TEXT NOT NULL,
    metadata   JSONB,
    embedding  vector(384),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS memory_run_col_idx ON memory (run_id, collection);

CREATE TABLE IF NOT EXISTS trades (
    id        SERIAL PRIMARY KEY,
    run_id    TEXT NOT NULL,
    date      TEXT NOT NULL,
    symbol    TEXT NOT NULL,
    action    TEXT NOT NULL,
    quantity  REAL NOT NULL,
    price     REAL NOT NULL,
    reasoning TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS trades_run_idx ON trades (run_id);

CREATE TABLE IF NOT EXISTS portfolio_state (
    run_id   TEXT NOT NULL,
    symbol   TEXT NOT NULL,
    position REAL NOT NULL DEFAULT 0,
    cash     REAL NOT NULL,
    PRIMARY KEY (run_id, symbol)
);
"""


def init_schema() -> None:
    """스키마 생성 + user1~4 초기 계정 생성 (비밀번호 = 아이디)."""
    import bcrypt  # noqa: PLC0415

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SCHEMA)

        for i in range(1, 5):
            username = f"user{i}"
            pw_hash = bcrypt.hashpw(username.encode(), bcrypt.gensalt()).decode()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (username, password_hash)
                    VALUES (%s, %s)
                    ON CONFLICT (username) DO NOTHING
                    """,
                    (username, pw_hash),
                )
