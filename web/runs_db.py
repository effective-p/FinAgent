"""백테스트 실행 결과 영구 저장 — PostgreSQL."""
from __future__ import annotations

import json
from typing import Optional

from web.db import get_conn


def create_run(
    run_id: str,
    symbol: str,
    stock_name: str,
    start_date: str,
    end_date: str,
    initial_cash: float,
    trader_preference: str,
    user_id: int | None = None,
    llm_config_id: int | None = None,
    status: str = "running",
    # 하위 호환: 더 이상 사용 안 하지만 기존 호출자가 넘기는 경우 무시
    job_data_dir: str | None = None,
    trace_dir: str | None = None,
) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO runs
                    (id, user_id, symbol, stock_name, start_date, end_date,
                     initial_cash, trader_preference, llm_config_id, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO NOTHING
                """,
                (run_id, user_id, symbol, stock_name, start_date, end_date,
                 initial_cash, trader_preference, llm_config_id, status),
            )


def update_run_done(run_id: str, result: dict) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE runs SET status='done', result_json=%s WHERE id=%s",
                (json.dumps(result, ensure_ascii=False), run_id),
            )


def update_run_error(run_id: str, error: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE runs SET status='error', error_msg=%s WHERE id=%s",
                (error, run_id),
            )


def update_run_running(run_id: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE runs SET status='running', error_msg=NULL WHERE id=%s",
                (run_id,),
            )


def get_resume_info(run_id: str) -> Optional[dict]:
    """재실행에 필요한 run 파라미터와 마지막 거래일을 반환한다."""
    import datetime as _dt  # noqa: PLC0415
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.symbol, r.stock_name, r.start_date, r.end_date,
                       r.initial_cash, r.trader_preference, r.llm_config_id,
                       MAX(t.date) as last_trade_date, r.user_id
                FROM runs r
                LEFT JOIN trades t ON t.run_id = r.id
                WHERE r.id = %s
                GROUP BY r.symbol, r.stock_name, r.start_date, r.end_date,
                         r.initial_cash, r.trader_preference, r.llm_config_id, r.user_id
                """,
                (run_id,),
            )
            row = cur.fetchone()
    if not row:
        return None

    def _to_date(v):
        if v is None:
            return None
        return _dt.date.fromisoformat(str(v)) if isinstance(v, str) else v

    return {
        "symbol": row[0],
        "stock_name": row[1],
        "start_date": _to_date(row[2]),
        "end_date": _to_date(row[3]),
        "initial_cash": float(row[4]),
        "trader_preference": row[5],
        "llm_config_id": row[6],
        "last_trade_date": _to_date(row[7]),
        "user_id": row[8],
    }


def list_queued_runs() -> list[dict]:
    """status='queued'로 남은 run들을 등록 순서대로 반환한다(재시작 복구용)."""
    import datetime as _dt  # noqa: PLC0415
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id, r.symbol, r.stock_name, r.start_date, r.end_date,
                       r.initial_cash, r.trader_preference,
                       lc.provider, lc.model, lc.api_key, lc.base_url
                FROM runs r
                LEFT JOIN llm_configs lc ON lc.id = r.llm_config_id
                WHERE r.status = 'queued'
                ORDER BY r.created_at ASC
                """
            )
            rows = cur.fetchall()

    def _to_date(v):
        return _dt.date.fromisoformat(str(v)) if isinstance(v, str) else v

    return [
        {
            "run_id": row[0],
            "symbol": row[1],
            "stock_name": row[2],
            "start": _to_date(row[3]),
            "end": _to_date(row[4]),
            "initial_cash": float(row[5]),
            "trader_preference": row[6],
            "llm": {"provider": row[7], "model": row[8],
                    "api_key": row[9] or None, "base_url": row[10]},
        }
        for row in rows
    ]


def claim_queued_run(run_id: str) -> bool:
    """'queued' 상태인 run을 원자적으로 'running'으로 전환한다.

    삭제(취소)되었거나 이미 다른 상태면 0행이 갱신되어 False를 반환한다.
    get_run_status 후 update 하는 TOCTOU 경쟁을 피한다.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE runs SET status='running', error_msg=NULL "
                "WHERE id=%s AND status='queued'",
                (run_id,),
            )
            return cur.rowcount > 0


def fail_stale_running() -> int:
    """서버 재시작 시 고아가 된 'running' run들을 'error'로 표시한다.

    새 프로세스 시작 시점에는 실제로 실행 중인 백테스트가 없으므로
    'running'으로 남아 있는 건 모두 이전 프로세스에서 중단된 것이다.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE runs SET status='error', error_msg='서버 재시작으로 중단됨' "
                "WHERE status='running'"
            )
            return cur.rowcount


def delete_run(run_id: str) -> bool:
    """run_id에 해당하는 실행 기록과 관련 데이터를 삭제한다. 성공 시 True 반환."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM trades WHERE run_id = %s", (run_id,))
            cur.execute("DELETE FROM portfolio_state WHERE run_id = %s", (run_id,))
            cur.execute("DELETE FROM runs WHERE id = %s", (run_id,))
            return cur.rowcount > 0


def list_runs() -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id, r.symbol, r.stock_name, r.start_date, r.end_date,
                       r.initial_cash, r.trader_preference, r.status,
                       r.error_msg, r.created_at, r.result_json,
                       u.username,
                       lc.provider, lc.model
                FROM runs r
                LEFT JOIN users u ON u.id = r.user_id
                LEFT JOIN llm_configs lc ON lc.id = r.llm_config_id
                ORDER BY r.created_at DESC
                """
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]

    result = []
    for row in rows:
        item = dict(zip(cols, row))
        item["created_at"] = str(item["created_at"])
        rj = item.pop("result_json", None)
        if rj:
            try:
                res = json.loads(rj)
                for k in ("total_return_pct", "sharpe_ratio", "max_drawdown_pct",
                          "benchmark_return_pct", "buy_count", "sell_count", "hold_count"):
                    item[k] = res.get(k)
            except Exception:
                pass
        result.append(item)
    return result


def get_run(run_id: str) -> Optional[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.*, u.username, lc.provider, lc.model
                FROM runs r
                LEFT JOIN users u ON u.id = r.user_id
                LEFT JOIN llm_configs lc ON lc.id = r.llm_config_id
                WHERE r.id = %s
                """,
                (run_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]

    item = dict(zip(cols, row))
    item["created_at"] = str(item["created_at"])
    rj = item.pop("result_json", None)
    item["result"] = json.loads(rj) if rj else None
    return item


def init_db() -> None:
    """하위 호환 — app.py 가 init_schema() 로 전환하기 전까지 유지."""
    pass
