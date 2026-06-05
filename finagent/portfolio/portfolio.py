from __future__ import annotations

import logging
from datetime import date
from typing import Dict, List

from finagent.utils.schemas import PortfolioState, TradeAction
from web.db import get_conn

logger = logging.getLogger(__name__)

BUY_RATIO = 0.5


class Portfolio:
    """현금·포지션·거래 내역을 PostgreSQL로 관리한다. run_id로 실행 간 격리."""

    def __init__(self, run_id: str, symbol: str, initial_cash: float) -> None:
        self.run_id = run_id
        self.symbol = symbol
        self._init_state(initial_cash)

    def _init_state(self, initial_cash: float) -> None:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO portfolio_state (run_id, symbol, position, cash)
                    VALUES (%s, %s, 0, %s)
                    ON CONFLICT (run_id, symbol) DO NOTHING
                    """,
                    (self.run_id, self.symbol, initial_cash),
                )

    # ------------------------------------------------------------------
    # 조회
    # ------------------------------------------------------------------

    def get_position(self) -> float:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT position FROM portfolio_state WHERE run_id=%s AND symbol=%s",
                    (self.run_id, self.symbol),
                )
                row = cur.fetchone()
        return float(row[0]) if row else 0.0

    def get_cash(self) -> float:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT cash FROM portfolio_state WHERE run_id=%s AND symbol=%s",
                    (self.run_id, self.symbol),
                )
                row = cur.fetchone()
        return float(row[0]) if row else 0.0

    def get_portfolio_value(self, current_price: float) -> float:
        return self.get_cash() + self.get_position() * current_price

    def get_state(self, current_price: float) -> PortfolioState:
        return PortfolioState(
            symbol=self.symbol,
            position=self.get_position(),
            cash=self.get_cash(),
            total_value=self.get_portfolio_value(current_price),
        )

    def recent_actions(self, n: int = 14) -> List[TradeAction]:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT date, symbol, action, quantity, price, reasoning
                    FROM trades
                    WHERE run_id = %s AND symbol = %s
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (self.run_id, self.symbol, n),
                )
                rows = cur.fetchall()
        return [
            TradeAction(
                action=r[2], quantity=r[3], price=r[4],
                date=date.fromisoformat(r[0]), reasoning=r[5] or "",
            )
            for r in reversed(rows)
        ]

    def get_all_trades(self) -> List[TradeAction]:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT date, symbol, action, quantity, price, reasoning
                    FROM trades WHERE run_id = %s AND symbol = %s ORDER BY id ASC
                    """,
                    (self.run_id, self.symbol),
                )
                rows = cur.fetchall()
        return [
            TradeAction(
                action=r[2], quantity=r[3], price=r[4],
                date=date.fromisoformat(r[0]), reasoning=r[5] or "",
            )
            for r in rows
        ]

    def get_returns(self, current_price: float, initial_cash: float) -> Dict:
        total_value = self.get_portfolio_value(current_price)
        total_return = (total_value - initial_cash) / initial_cash * 100
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT action, COUNT(*) FROM trades WHERE run_id=%s AND symbol=%s GROUP BY action",
                    (self.run_id, self.symbol),
                )
                counts = {r[0]: r[1] for r in cur.fetchall()}
        return {
            "total_value": round(total_value, 2),
            "total_return_pct": round(total_return, 2),
            "buy_count": counts.get("BUY", 0),
            "sell_count": counts.get("SELL", 0),
            "hold_count": counts.get("HOLD", 0),
        }

    # ------------------------------------------------------------------
    # 거래 실행
    # ------------------------------------------------------------------

    def execute(self, action: str, price: float, target_date: date, reasoning: str = "") -> None:
        action = action.upper()
        if action not in ("BUY", "SELL", "HOLD"):
            raise ValueError(f"Invalid action: {action}")

        cash = self.get_cash()
        position = self.get_position()

        if action == "BUY":
            quantity = int(cash * BUY_RATIO / price)
            if quantity < 1:
                logger.info("BUY skipped: 1주 매수 불가 (cash=%.0f, price=%.0f)", cash, price)
                return
            self._update_state(position + quantity, cash - quantity * price)
            self._record_trade(target_date, "BUY", quantity, price, reasoning)
            logger.info("BUY %d @ %.0f | cash: %.0f -> %.0f", quantity, price, cash, cash - quantity * price)

        elif action == "SELL":
            if position < 1e-8:
                logger.info("SELL skipped: no position to sell")
                return
            self._update_state(0.0, cash + position * price)
            self._record_trade(target_date, "SELL", position, price, reasoning)
            logger.info("SELL %.4f @ %.0f | cash: %.0f -> %.0f", position, price, cash, cash + position * price)

        else:
            self._record_trade(target_date, "HOLD", 0.0, price, reasoning)
            logger.info("HOLD @ %.2f", price)

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    def _update_state(self, position: float, cash: float) -> None:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE portfolio_state SET position=%s, cash=%s WHERE run_id=%s AND symbol=%s",
                    (position, cash, self.run_id, self.symbol),
                )

    def _record_trade(self, trade_date: date, action: str, quantity: float, price: float, reasoning: str) -> None:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO trades (run_id, date, symbol, action, quantity, price, reasoning) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (self.run_id, trade_date.isoformat(), self.symbol, action, quantity, price, reasoning),
                )
