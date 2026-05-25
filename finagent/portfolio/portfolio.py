from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import date
from typing import Dict, List

from finagent.utils.schemas import PortfolioState, TradeAction

logger = logging.getLogger(__name__)

# 매수 시 현금의 몇 %를 사용할지
BUY_RATIO = 0.5

# 논문 §5.1: fixed transaction cost of 0.1% for both buying and selling
TRANSACTION_COST_RATE = 0.001


class Portfolio:
    """현금·포지션·거래 내역을 SQLite로 관리한다."""

    def __init__(
        self,
        symbol: str,
        initial_cash: float,
        db_path: str = "portfolio.db",
        reset: bool = False,
    ) -> None:
        self.symbol = symbol
        self.db_path = db_path
        self._init_db(initial_cash, reset=reset)

    # ------------------------------------------------------------------
    # DB 초기화
    # ------------------------------------------------------------------

    def _init_db(self, initial_cash: float, reset: bool = False) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    date      TEXT    NOT NULL,
                    symbol    TEXT    NOT NULL,
                    action    TEXT    NOT NULL,
                    quantity  REAL    NOT NULL,
                    price     REAL    NOT NULL,
                    reasoning TEXT    DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS state (
                    symbol   TEXT PRIMARY KEY,
                    position REAL    NOT NULL DEFAULT 0,
                    cash     REAL    NOT NULL
                )
            """)
            if reset:
                # 백테스트 재실행 시 이전 데이터 초기화
                conn.execute("DELETE FROM trades WHERE symbol = ?", (self.symbol,))
                conn.execute("DELETE FROM state WHERE symbol = ?", (self.symbol,))
                logger.info("Portfolio reset for symbol=%s", self.symbol)

            conn.execute(
                "INSERT OR IGNORE INTO state (symbol, position, cash) VALUES (?, 0, ?)",
                (self.symbol, initial_cash),
            )

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 조회
    # ------------------------------------------------------------------

    def get_position(self) -> float:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT position FROM state WHERE symbol = ?", (self.symbol,)
            ).fetchone()
        return float(row["position"]) if row else 0.0

    def get_cash(self) -> float:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT cash FROM state WHERE symbol = ?", (self.symbol,)
            ).fetchone()
        return float(row["cash"]) if row else 0.0

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
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT date, symbol, action, quantity, price, reasoning
                   FROM trades
                   WHERE symbol = ?
                   ORDER BY id DESC
                   LIMIT ?""",
                (self.symbol, n),
            ).fetchall()
        return [
            TradeAction(
                action=r["action"],
                quantity=r["quantity"],
                price=r["price"],
                date=date.fromisoformat(r["date"]),
                reasoning=r["reasoning"] or "",
            )
            for r in reversed(rows)
        ]

    def get_all_trades(self) -> List[TradeAction]:
        """전체 거래 내역을 날짜 오름차순으로 반환한다."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT date, symbol, action, quantity, price, reasoning
                   FROM trades WHERE symbol = ? ORDER BY id ASC""",
                (self.symbol,),
            ).fetchall()
        return [
            TradeAction(
                action=r["action"],
                quantity=r["quantity"],
                price=r["price"],
                date=date.fromisoformat(r["date"]),
                reasoning=r["reasoning"] or "",
            )
            for r in rows
        ]

    def get_returns(self, current_price: float, initial_cash: float) -> Dict:
        """누적 수익률 및 기본 통계."""
        total_value = self.get_portfolio_value(current_price)
        total_return = (total_value - initial_cash) / initial_cash * 100

        with self._conn() as conn:
            trades = conn.execute(
                "SELECT action, COUNT(*) as cnt FROM trades WHERE symbol = ? GROUP BY action",
                (self.symbol,),
            ).fetchall()

        action_counts = {r["action"]: r["cnt"] for r in trades}
        return {
            "total_value": round(total_value, 2),
            "total_return_pct": round(total_return, 2),
            "buy_count": action_counts.get("BUY", 0),
            "sell_count": action_counts.get("SELL", 0),
            "hold_count": action_counts.get("HOLD", 0),
        }

    # ------------------------------------------------------------------
    # 거래 실행
    # ------------------------------------------------------------------

    def execute(
        self,
        action: str,
        price: float,
        target_date: date,
        reasoning: str = "",
    ) -> None:
        """BUY / SELL / HOLD를 실행하고 DB에 기록한다."""
        action = action.upper()
        if action not in ("BUY", "SELL", "HOLD"):
            raise ValueError(f"Invalid action: {action}")

        cash = self.get_cash()
        position = self.get_position()

        if action == "BUY":
            available = cash * BUY_RATIO
            # 수수료 포함 실질 매수가 산정 후 정수 수량 결정
            quantity = int(available / (price * (1 + TRANSACTION_COST_RATE)))
            if quantity < 1:
                logger.info("BUY skipped: 1주 매수 불가 (cash=%.0f, price=%.0f)", cash, price)
                return
            fee = quantity * price * TRANSACTION_COST_RATE
            new_cash = cash - quantity * price - fee
            new_position = position + quantity
            self._update_state(new_position, new_cash)
            self._record_trade(target_date, "BUY", quantity, price, reasoning)
            logger.info(
                "BUY %d @ %.0f fee=%.0f | cash: %.0f → %.0f",
                quantity, price, fee, cash, new_cash,
            )

        elif action == "SELL":
            if position < 1e-8:
                logger.info("SELL skipped: no position to sell")
                return
            fee = position * price * TRANSACTION_COST_RATE
            new_cash = cash + position * price - fee
            self._update_state(0.0, new_cash)
            self._record_trade(target_date, "SELL", position, price, reasoning)
            logger.info(
                "SELL %.4f @ %.0f fee=%.0f | cash: %.0f → %.0f",
                position, price, fee, cash, new_cash,
            )

        else:  # HOLD
            self._record_trade(target_date, "HOLD", 0.0, price, reasoning)
            logger.info("HOLD @ %.2f", price)

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    def _update_state(self, position: float, cash: float) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE state SET position = ?, cash = ? WHERE symbol = ?",
                (position, cash, self.symbol),
            )

    def _record_trade(
        self,
        trade_date: date,
        action: str,
        quantity: float,
        price: float,
        reasoning: str,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO trades (date, symbol, action, quantity, price, reasoning) VALUES (?,?,?,?,?,?)",
                (trade_date.isoformat(), self.symbol, action, quantity, price, reasoning),
            )
