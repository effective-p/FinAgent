from __future__ import annotations

import logging

import pandas as pd
import pandas_ta as ta

from finagent.utils.schemas import TechnicalSignals

logger = logging.getLogger(__name__)


def get_technical_signals(df: pd.DataFrame) -> TechnicalSignals:
    """OHLCV DataFrame으로부터 MACD / KDJ+RSI / ZMR 시그널을 계산한다.

    Args:
        df: yfinance 형식의 OHLCV DataFrame (최소 26봉 이상 권장)

    Returns:
        TechnicalSignals (각 시그널 + LLM 주입용 텍스트)
    """
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    macd_signal, macd_detail = _calc_macd(df)
    kdj_rsi_signal, kdj_rsi_detail = _calc_kdj_rsi(df)
    zmr_signal, zmr_detail = _calc_zmr(df)

    signal_text = "\n".join([macd_detail, kdj_rsi_detail, zmr_detail])

    return TechnicalSignals(
        macd_signal=macd_signal,
        kdj_rsi_signal=kdj_rsi_signal,
        zmr_signal=zmr_signal,
        signal_text=signal_text,
    )


# ------------------------------------------------------------------
# MACD (12/26/9)
# ------------------------------------------------------------------

def _calc_macd(df: pd.DataFrame) -> tuple[str, str]:
    macd_df = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd_df is None or macd_df.empty:
        return "HOLD", "MACD: HOLD (insufficient data)"

    macd_col = [c for c in macd_df.columns if c.startswith("MACD_") and "Signal" not in c and "Hist" not in c]
    sig_col = [c for c in macd_df.columns if "MACDs" in c or "Signal" in c]

    if not macd_col or not sig_col:
        return "HOLD", "MACD: HOLD (column parse error)"

    macd_line = macd_df[macd_col[0]]
    signal_line = macd_df[sig_col[0]]

    if len(macd_line) < 2:
        return "HOLD", "MACD: HOLD (insufficient data)"

    prev_diff = macd_line.iloc[-2] - signal_line.iloc[-2]
    curr_diff = macd_line.iloc[-1] - signal_line.iloc[-1]
    curr_macd = round(macd_line.iloc[-1], 4)

    if prev_diff < 0 and curr_diff >= 0:
        signal = "BUY"
        detail = f"MACD: BUY signal (golden cross, MACD={curr_macd})"
    elif prev_diff > 0 and curr_diff <= 0:
        signal = "SELL"
        detail = f"MACD: SELL signal (dead cross, MACD={curr_macd})"
    else:
        direction = "above" if curr_diff > 0 else "below"
        signal = "HOLD"
        detail = f"MACD: HOLD (MACD={curr_macd}, {direction} signal line)"

    return signal, detail


# ------------------------------------------------------------------
# KDJ (9,3,3) + RSI (14)
# ------------------------------------------------------------------

def _calc_kdj_rsi(df: pd.DataFrame) -> tuple[str, str]:
    # KDJ: pandas_ta의 stoch을 K/D로 사용, J = 3K - 2D
    stoch = ta.stoch(df["high"], df["low"], df["close"], k=9, d=3, smooth_k=3)
    rsi_series = ta.rsi(df["close"], length=14)

    if stoch is None or rsi_series is None or stoch.empty or rsi_series.empty:
        return "HOLD", "KDJ+RSI: HOLD (insufficient data)"

    k_col = [c for c in stoch.columns if c.startswith("STOCHk")]
    d_col = [c for c in stoch.columns if c.startswith("STOCHd")]
    if not k_col or not d_col:
        return "HOLD", "KDJ+RSI: HOLD (column parse error)"

    k = round(stoch[k_col[0]].iloc[-1], 2)
    d = round(stoch[d_col[0]].iloc[-1], 2)
    j = round(3 * k - 2 * d, 2)
    rsi = round(rsi_series.iloc[-1], 2)

    if k > 80 and rsi > 70:
        signal = "SELL"
        detail = f"KDJ+RSI: SELL signal (K={k}, J={j}, RSI={rsi}, overbought)"
    elif k < 20 and rsi < 30:
        signal = "BUY"
        detail = f"KDJ+RSI: BUY signal (K={k}, J={j}, RSI={rsi}, oversold)"
    else:
        signal = "HOLD"
        detail = f"KDJ+RSI: HOLD (K={k}, J={j}, RSI={rsi})"

    return signal, detail


# ------------------------------------------------------------------
# ZMR — Z-score of price vs 20-day MA
# ------------------------------------------------------------------

def _calc_zmr(df: pd.DataFrame, window: int = 20) -> tuple[str, str]:
    close = df["close"]
    if len(close) < window:
        return "HOLD", "ZMR: HOLD (insufficient data)"

    ma = close.rolling(window).mean()
    std = close.rolling(window).std()

    last_ma = ma.iloc[-1]
    last_std = std.iloc[-1]
    last_close = close.iloc[-1]

    if last_std == 0 or pd.isna(last_std):
        return "HOLD", "ZMR: HOLD (zero std)"

    z = round((last_close - last_ma) / last_std, 3)

    if z < -1.5:
        signal = "BUY"
        detail = f"ZMR: BUY signal (z-score={z}, price undervalued vs MA{window})"
    elif z > 1.5:
        signal = "SELL"
        detail = f"ZMR: SELL signal (z-score={z}, price overvalued vs MA{window})"
    else:
        signal = "HOLD"
        detail = f"ZMR: HOLD (z-score={z}, within normal range)"

    return signal, detail
