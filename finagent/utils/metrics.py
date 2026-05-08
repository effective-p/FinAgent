from __future__ import annotations

import logging
import math
from typing import List

import matplotlib
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

from finagent.utils.schemas import TradeAction

logger = logging.getLogger(__name__)

matplotlib.use("Agg")

matplotlib.rcParams["axes.unicode_minus"] = False

TRADING_DAYS_PER_YEAR = 252


# ---------------------------------------------------------------------------
# Equity curve
# ---------------------------------------------------------------------------

def compute_equity_curve(
    trades: List[TradeAction],
    price_df: pd.DataFrame,
    initial_cash: float,
    buy_ratio: float = 0.5,
) -> pd.Series:
    """Trade 히스토리를 재현하여 일별 총 자산가치 시리즈를 반환한다.

    index: date, values: 총 포트폴리오 가치(현금 + 포지션 평가액)
    """
    sorted_trades = sorted(trades, key=lambda t: t.date)
    cash = initial_cash
    position = 0.0
    trade_idx = 0
    values: dict = {}

    for ts, row in price_df.iterrows():
        day = ts.date()
        close = float(row["Close"])

        while trade_idx < len(sorted_trades) and sorted_trades[trade_idx].date <= day:
            t = sorted_trades[trade_idx]
            if t.action == "BUY":
                spent = cash * buy_ratio
                if spent > 0:
                    position += spent / t.price
                    cash -= spent
            elif t.action == "SELL":
                cash += position * t.price
                position = 0.0
            trade_idx += 1

        values[day] = cash + position * close

    return pd.Series(values)


def compute_benchmark(
    price_df: pd.DataFrame,
    initial_cash: float,
) -> pd.Series:
    """Buy-and-hold 벤치마크: 첫 날 종가로 전량 매수 후 보유."""
    if price_df.empty:
        return pd.Series(dtype=float)

    first_close = float(price_df["Close"].iloc[0])
    shares = initial_cash / first_close
    values = {ts.date(): shares * float(row["Close"]) for ts, row in price_df.iterrows()}
    return pd.Series(values)


# ---------------------------------------------------------------------------
# 성과 지표
# ---------------------------------------------------------------------------

def compute_performance(
    equity_curve: pd.Series,
    initial_cash: float,
    risk_free_rate: float = 0.03,
) -> dict:
    """수익률 / Sharpe / MDD 등 성과 지표를 계산한다.

    Args:
        equity_curve: compute_equity_curve() 반환값
        initial_cash: 초기 투자 금액
        risk_free_rate: 연간 무위험 수익률 (기본 3%)
    """
    if equity_curve.empty or len(equity_curve) < 2:
        return {}

    final_value = float(equity_curve.iloc[-1])
    n_days = len(equity_curve)
    years = n_days / TRADING_DAYS_PER_YEAR

    total_return_pct = (final_value - initial_cash) / initial_cash * 100
    annualized_return_pct = (
        ((final_value / initial_cash) ** (1 / years) - 1) * 100 if years > 0 else 0.0
    )

    daily_returns = equity_curve.pct_change().dropna()
    volatility_annual = float(daily_returns.std() * math.sqrt(TRADING_DAYS_PER_YEAR) * 100)

    rf_daily = (1 + risk_free_rate) ** (1 / TRADING_DAYS_PER_YEAR) - 1
    excess = daily_returns - rf_daily
    sharpe = (
        float(excess.mean() / excess.std() * math.sqrt(TRADING_DAYS_PER_YEAR))
        if excess.std() > 0 else 0.0
    )

    rolling_max = equity_curve.cummax()
    drawdown = (equity_curve - rolling_max) / rolling_max
    max_drawdown_pct = float(drawdown.min() * 100)

    return {
        "total_return_pct": round(total_return_pct, 2),
        "annualized_return_pct": round(annualized_return_pct, 2),
        "sharpe_ratio": round(sharpe, 3),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "volatility_annual_pct": round(volatility_annual, 2),
        "final_value": round(final_value, 2),
        "n_trading_days": n_days,
    }


# ---------------------------------------------------------------------------
# 시각화
# ---------------------------------------------------------------------------

def plot_performance(
    equity_curve: pd.Series,
    benchmark: pd.Series,
    trades: List[TradeAction],
    output_path: str,
) -> str:
    """FinAgent 자산곡선 vs Buy&Hold + Drawdown 차트를 PNG로 저장한다."""

    # macOS 한글 폰트 설정 (없으면 기본 폰트 유지)
    for _font in ["Pretendard"]:
        try:
            plt.rcParams["font.family"] = _font
            break
        except Exception:
            logger.info("Font setting error")
            continue

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(14, 8),
        gridspec_kw={"height_ratios": [3, 1]},
        sharex=True,
    )

    dates_eq = [pd.Timestamp(d) for d in equity_curve.index]
    dates_bm = [pd.Timestamp(d) for d in benchmark.index]

    ax1.plot(dates_eq, equity_curve.values, label="FinAgent", color="#1f77b4", linewidth=1.5)
    ax1.plot(dates_bm, benchmark.values, label="Buy & Hold", color="#ff7f0e",
             linewidth=1.5, linestyle="--")

    # BUY/SELL 수직선
    date_set = {pd.Timestamp(d) for d in equity_curve.index}
    for t in trades:
        ts = pd.Timestamp(t.date)
        if ts not in date_set:
            continue
        if t.action == "BUY":
            ax1.axvline(ts, color="green", alpha=0.35, linewidth=1)
        elif t.action == "SELL":
            ax1.axvline(ts, color="red", alpha=0.35, linewidth=1)

    ax1.set_ylabel("포트폴리오 가치 (원)")
    ax1.legend(loc="upper left")
    ax1.set_title("FinAgent 백테스팅 성과")
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:,.0f}"))
    ax1.grid(True, alpha=0.3)

    # Drawdown
    rolling_max = equity_curve.cummax()
    drawdown = (equity_curve - rolling_max) / rolling_max * 100
    ax2.fill_between(dates_eq, drawdown.values, 0, color="red", alpha=0.4, label="Drawdown")
    ax2.set_ylabel("낙폭 (%)")
    ax2.set_xlabel("날짜")
    ax2.legend(loc="lower left")
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax2.xaxis.set_major_locator(mdates.MonthLocator())
    plt.setp(ax2.get_xticklabels(), rotation=30, ha="right")

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return output_path
