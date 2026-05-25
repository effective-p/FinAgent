"""기존 portfolio.db 데이터로 성과 차트만 재생성한다."""
from dotenv import load_dotenv
load_dotenv()

import sqlite3
from datetime import date
from finagent.utils.schemas import TradeAction
from finagent.utils.metrics import compute_equity_curve, compute_benchmark, plot_performance
from pykrx import stock

SYMBOL = "373220"
START = "20260401"
END = "20260430"
INITIAL_CASH = 10_000_000

conn = sqlite3.connect("portfolio.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT date, action, quantity, price, reasoning FROM trades WHERE symbol = ? ORDER BY id ASC",
    (SYMBOL,),
).fetchall()
conn.close()

trades = [
    TradeAction(
        action=r["action"],
        quantity=r["quantity"],
        price=r["price"],
        date=date.fromisoformat(r["date"]),
        reasoning=r["reasoning"] or "",
    )
    for r in rows
]
print(f"거래 내역: {len(trades)}건")

price_df = stock.get_market_ohlcv_by_date(START, END, SYMBOL)
price_df = price_df.rename(columns={"시가": "Open", "고가": "High", "저가": "Low", "종가": "Close", "거래량": "Volume"})
print(f"가격 데이터: {len(price_df)}일")

equity = compute_equity_curve(trades, price_df, INITIAL_CASH)
benchmark = compute_benchmark(price_df, INITIAL_CASH)
out = plot_performance(
    equity, benchmark, trades,
    f"charts/performance_{SYMBOL}_2026-04-01_2026-04-30.png"
)
print(f"차트 저장 완료: {out}")
