"""백테스트 실행 결과 영구 저장 — SQLite finagent_runs.db."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

_DB_PATH = "finagent_runs.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id                TEXT PRIMARY KEY,
    symbol            TEXT NOT NULL,
    stock_name        TEXT NOT NULL,
    start_date        TEXT NOT NULL,
    end_date          TEXT NOT NULL,
    initial_cash      REAL NOT NULL,
    trader_preference TEXT NOT NULL,
    status            TEXT NOT NULL,
    result_json       TEXT,
    error_msg         TEXT,
    job_data_dir      TEXT NOT NULL,
    trace_dir         TEXT,
    created_at        TEXT NOT NULL
)
"""


@contextmanager
def _conn():
    con = sqlite3.connect(_DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    with _conn() as con:
        con.execute(_SCHEMA)


def create_run(
    run_id: str,
    symbol: str,
    stock_name: str,
    start_date: str,
    end_date: str,
    initial_cash: float,
    trader_preference: str,
    job_data_dir: str,
    trace_dir: str | None = None,
) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, symbol, stock_name, start_date, end_date, initial_cash,
             trader_preference, "running", None, None,
             job_data_dir, trace_dir, datetime.now().isoformat()),
        )


def update_run_done(run_id: str, result: dict) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE runs SET status=?, result_json=? WHERE id=?",
            ("done", json.dumps(result, ensure_ascii=False), run_id),
        )


def update_run_error(run_id: str, error: str) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE runs SET status=?, error_msg=? WHERE id=?",
            ("error", error, run_id),
        )


def list_runs() -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT id, symbol, stock_name, start_date, end_date, initial_cash, "
            "trader_preference, status, error_msg, created_at, result_json "
            "FROM runs ORDER BY created_at DESC"
        ).fetchall()
    result = []
    for r in rows:
        item = dict(r)
        # KPI 요약만 포함 (equity_curve 제외)
        if item.get("result_json"):
            try:
                res = json.loads(item["result_json"])
                item["total_return_pct"] = res.get("total_return_pct")
                item["sharpe_ratio"] = res.get("sharpe_ratio")
                item["max_drawdown_pct"] = res.get("max_drawdown_pct")
                item["benchmark_return_pct"] = res.get("benchmark_return_pct")
                item["buy_count"] = res.get("buy_count")
                item["sell_count"] = res.get("sell_count")
                item["hold_count"] = res.get("hold_count")
            except Exception:
                pass
        del item["result_json"]
        result.append(item)
    return result


def get_run(run_id: str) -> Optional[dict]:
    with _conn() as con:
        row = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    if item.get("result_json"):
        try:
            item["result"] = json.loads(item["result_json"])
        except Exception:
            item["result"] = None
    else:
        item["result"] = None
    del item["result_json"]
    return item
