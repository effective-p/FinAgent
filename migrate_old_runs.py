"""기존 job_data 결과를 finagent_runs.db 에 마이그레이션."""
import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

import numpy as np
from web import runs_db

JOBS = [
    {
        "run_id": "3ef46128-b406-482e-81a7-1a65e6d60bca",
        "symbol": "373220",
        "stock_name": "LG에너지솔루션",
        "start_date": "2026-04-01",
        "end_date": "2026-04-30",
        "initial_cash": 10_000_000,
        "trader_preference": "moderate",
    },
    {
        "run_id": "b6fabbcf-de9d-400e-9cea-2ee2c082339e",
        "symbol": "373220",
        "stock_name": "LG에너지솔루션",
        "start_date": "2026-04-01",
        "end_date": "2026-04-30",
        "initial_cash": 10_000_000,
        "trader_preference": "aggressive",
    },
]

BASE = os.path.join(os.path.dirname(__file__), "job_data")


def load_trades(job_dir: str):
    db_path = os.path.join(job_dir, "portfolio.db")
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    trades = con.execute(
        "SELECT date, action, quantity, price, reasoning FROM trades ORDER BY id"
    ).fetchall()
    con.close()
    return [dict(t) for t in trades]


def build_equity(trades, initial_cash: float):
    cash = initial_cash
    position = 0.0
    equity = []
    for t in trades:
        qty = t["quantity"] or 0.0
        price = t["price"]
        if t["action"] == "BUY" and qty > 0:
            cash -= qty * price
            position += qty
        elif t["action"] == "SELL":
            cash += position * price
            position = 0.0
        equity.append({"date": t["date"], "value": round(cash + position * price, 2)})
    return equity


def build_benchmark(trades, initial_cash: float):
    if not trades:
        return []
    shares = initial_cash / trades[0]["price"]
    return [{"date": t["date"], "value": round(shares * t["price"], 2)} for t in trades]


def compute_kpis(equity_curve, benchmark_curve, trades, initial_cash):
    values = [p["value"] for p in equity_curve]
    final = values[-1]
    total_ret = (final - initial_cash) / initial_cash * 100
    years = len(values) / 252
    ann_ret = ((final / initial_cash) ** (1 / max(years, 0.001)) - 1) * 100

    daily_r = np.diff(values) / np.array(values[:-1]) if len(values) > 1 else np.array([0.0])
    vol = float(np.std(daily_r) * (252 ** 0.5) * 100)
    rf_daily = 0.03 / 252
    excess = daily_r - rf_daily
    sharpe = float(np.mean(excess) / np.std(excess) * (252 ** 0.5)) if np.std(excess) > 0 else 0.0
    neg = daily_r[daily_r < 0]
    down_std = float(np.std(neg) * (252 ** 0.5) * 100) if len(neg) > 0 else 0.001
    sortino = ann_ret / down_std if down_std > 0 else 0.0
    cum_max = np.maximum.accumulate(values)
    mdd = float(((np.array(values) - cum_max) / cum_max).min() * 100)
    calmar = ann_ret / abs(mdd) if mdd != 0 else 0.0

    bh = [p["value"] for p in benchmark_curve]
    bh_ret = (bh[-1] - initial_cash) / initial_cash * 100 if bh else 0.0

    return {
        "total_return_pct": round(total_ret, 4),
        "annualized_return_pct": round(ann_ret, 4),
        "sharpe_ratio": round(sharpe, 4),
        "sortino_ratio": round(sortino, 4),
        "calmar_ratio": round(calmar, 4),
        "max_drawdown_pct": round(mdd, 4),
        "annualized_volatility_pct": round(vol, 4),
        "benchmark_return_pct": round(bh_ret, 4),
        "buy_count": sum(1 for t in trades if t["action"] == "BUY"),
        "sell_count": sum(1 for t in trades if t["action"] == "SELL"),
        "hold_count": sum(1 for t in trades if t["action"] == "HOLD"),
        "equity_curve": equity_curve,
        "benchmark_curve": benchmark_curve,
    }


def migrate():
    runs_db.init_db()
    for job in JOBS:
        run_id = job["run_id"]
        job_dir = os.path.join(BASE, run_id)

        if runs_db.get_run(run_id):
            print(f"[SKIP] {run_id[:8]} -already in DB")
            continue
        if not os.path.isdir(job_dir):
            print(f"[SKIP] {run_id[:8]} -dir not found")
            continue

        trades = load_trades(job_dir)
        if not trades:
            print(f"[SKIP] {run_id[:8]} -no trades")
            continue

        equity_curve = build_equity(trades, job["initial_cash"])
        benchmark_curve = build_benchmark(trades, job["initial_cash"])
        kpis = compute_kpis(equity_curve, benchmark_curve, trades, job["initial_cash"])

        runs_db.create_run(
            run_id=run_id,
            symbol=job["symbol"],
            stock_name=job["stock_name"],
            start_date=job["start_date"],
            end_date=job["end_date"],
            initial_cash=job["initial_cash"],
            trader_preference=job["trader_preference"],
            job_data_dir=job_dir,
            trace_dir=None,
        )
        runs_db.update_run_done(run_id, kpis)
        print(
            f"[OK] {run_id[:8]} {job['symbol']} {job['start_date']}~{job['end_date']} "
            f"ret={kpis['total_return_pct']:.2f}%"
        )


if __name__ == "__main__":
    migrate()
