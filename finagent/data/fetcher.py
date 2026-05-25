from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List
from urllib.parse import quote

import feedparser
import mplfinance as mpf
import pandas as pd
from pykrx import stock as krx

from finagent.utils.schemas import NewsItem, TradeAction

logger = logging.getLogger(__name__)


class DataFetcher:
    def __init__(self, chart_dir: str = "charts") -> None:
        self.chart_dir = Path(chart_dir)
        self.chart_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 가격 데이터
    # ------------------------------------------------------------------

    def get_price_data(self, symbol: str, lookback_days: int = 60) -> pd.DataFrame:
        """pykrx로 KRX OHLCV 수집. symbol은 종목코드(예: '005930').
        index는 DatetimeIndex(timezone-naive).
        """
        end = date.today()
        start = end - timedelta(days=lookback_days)
        fromdate = start.strftime("%Y%m%d")
        todate = end.strftime("%Y%m%d")

        df = krx.get_market_ohlcv_by_date(fromdate, todate, symbol)

        if df is None or df.empty:
            raise ValueError(f"No price data for {symbol}")

        df = df.rename(columns={
            "시가": "Open",
            "고가": "High",
            "저가": "Low",
            "종가": "Close",
            "거래량": "Volume",
        })
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.index = pd.to_datetime(df.index)
        df.ffill(inplace=True)
        df.dropna(inplace=True)
        return df

    # ------------------------------------------------------------------
    # 뉴스 (구글 뉴스 RSS)
    # ------------------------------------------------------------------

    def get_news(
        self,
        symbol: str,
        stock_name: str,
        target_date: date,
        max_items: int = 10,
    ) -> List[NewsItem]:
        """뉴스 RSS에서 종목 관련 뉴스를 수집한다.

        Args:
            symbol: 티커 (로깅용, 실제 검색엔 미사용)
            stock_name: 한글 종목명 (예: "삼성전자")
            target_date: 기준일. ±7일 이내 뉴스만 반환
            max_items: 최대 반환 개수
        """
        query = quote(f"{stock_name}")
        url = f"https://news.google.com/rss/search?hl=ko&gl=KR&ie=UTF-8&q={query}"

        try:
            feed = feedparser.parse(url)
        except Exception as exc:
            logger.warning("News RSS fetch failed for %s: %s", stock_name, exc)
            return []

        news: List[NewsItem] = []
        for entry in feed.entries:
            if len(news) >= max_items:
                break

            # 발행일 파싱
            if entry.get("published_parsed"):
                published = datetime(*entry.published_parsed[:6])
            else:
                published = datetime.now()

            # 기준일 이전 7일만 허용 (미래 뉴스 look-ahead 방지)
            days_diff = (published.date() - target_date).days
            if days_diff > 0 or days_diff < -7:
                continue

            news.append(
                NewsItem(
                    title=_strip_html(entry.get("title", "")),
                    summary=_strip_html(entry.get("summary", "")),
                    published=published,
                    url=entry.get("link", ""),
                )
            )

        logger.info("Fetched %d news items for %s (%s)", len(news), stock_name, symbol)
        return news

    # ------------------------------------------------------------------
    # 투자자 동향 (한국 주식 특화)
    # ------------------------------------------------------------------

    def get_investor_trading(
        self,
        symbol: str,
        target_date: date,
        lookback_days: int = 5,
    ) -> str:
        """pykrx로 외국인/기관/개인 투자자별 순매수 금액을 텍스트로 반환한다.

        Args:
            symbol: KRX 종목코드
            target_date: 기준일 (이 날까지의 데이터만 사용)
            lookback_days: 최근 몇 거래일치를 집계할지
        """
        end = target_date
        start = target_date - timedelta(days=lookback_days + 14)  # 거래일 여유분
        fromdate = start.strftime("%Y%m%d")
        todate = end.strftime("%Y%m%d")

        try:
            df = krx.get_market_trading_value_by_investor(fromdate, todate, symbol)
            if df is None or df.empty:
                return "투자자 동향 데이터 없음"

            df = df.tail(lookback_days)

            def _find_col(keywords: list) -> str | None:
                for kw in keywords:
                    for col in df.columns:
                        if kw in str(col):
                            return col
                return None

            foreign_col = _find_col(["외국인합계", "외국인"])
            inst_col    = _find_col(["기관합계", "기관"])
            indiv_col   = _find_col(["개인"])

            lines = [f"[투자자별 순매수 금액 (최근 {len(df)}거래일, 억원)]"]
            for label, col in [("외국인", foreign_col), ("기관", inst_col), ("개인", indiv_col)]:
                if col and col in df.columns:
                    total_eok = df[col].sum() / 1e8
                    last_eok  = df[col].iloc[-1] / 1e8
                    sign = "▲ 매수우위" if total_eok >= 0 else "▼ 매도우위"
                    lines.append(
                        f"  {label}: 합계 {total_eok:+.0f}억  (당일 {last_eok:+.0f}억)  {sign}"
                    )

            return "\n".join(lines) if len(lines) > 1 else "투자자 동향 데이터 없음"

        except Exception as exc:
            logger.warning("투자자 데이터 조회 실패 %s: %s", symbol, exc)
            return "투자자 동향 데이터 없음"

    # ------------------------------------------------------------------
    # 기본 투자지표 (한국 주식 Expert Guidance 대체)
    # ------------------------------------------------------------------

    def get_fundamental_guidance(
        self,
        symbol: str,
        target_date: date,
        lookback_days: int = 60,
    ) -> str:
        """pykrx로 PER/PBR/배당수익률을 조회해 역사적 평균 대비 투자 신호를 생성한다.

        미국 주식의 'Expert Guidance' (Bloomberg/Seeking Alpha 애널리스트 리포트) 역할.
        """
        end = target_date
        start = target_date - timedelta(days=lookback_days + 30)
        fromdate = start.strftime("%Y%m%d")
        todate = end.strftime("%Y%m%d")

        try:
            df = krx.get_market_fundamental_by_date(fromdate, todate, symbol)
            if df is None or df.empty:
                return "기본 투자지표 데이터 없음"

            df.index = pd.to_datetime(df.index)
            df = df.loc[:pd.Timestamp(target_date)]
            if df.empty:
                return "기본 투자지표 데이터 없음"

            current = df.iloc[-1]
            historical = df.iloc[:-1]

            def _float(key: str) -> float:
                val = current.get(key, 0)
                try:
                    return float(val) if val and val == val else 0.0
                except (TypeError, ValueError):
                    return 0.0

            per = _float("PER")
            pbr = _float("PBR")
            div = _float("DIV")

            lines = [f"[기본 투자지표 분석 (기준일: {target_date}, 최근 {lookback_days}일 평균 대비)]"]

            if per > 0 and len(historical) >= 10:
                hist_per = historical["PER"].replace(0, float("nan")).dropna()
                if len(hist_per) >= 10:
                    avg_per = float(hist_per.mean())
                    if per < avg_per * 0.85:
                        sig = f"역사적 평균({avg_per:.1f}배) 대비 저평가 → BULLISH"
                    elif per > avg_per * 1.15:
                        sig = f"역사적 평균({avg_per:.1f}배) 대비 고평가 → BEARISH"
                    else:
                        sig = f"역사적 평균({avg_per:.1f}배) 수준 → NEUTRAL"
                    lines.append(f"  PER: {per:.1f}배 — {sig}")
                else:
                    lines.append(f"  PER: {per:.1f}배")
            elif per > 0:
                lines.append(f"  PER: {per:.1f}배")

            if pbr > 0 and len(historical) >= 10:
                hist_pbr = historical["PBR"].replace(0, float("nan")).dropna()
                if len(hist_pbr) >= 10:
                    avg_pbr = float(hist_pbr.mean())
                    if pbr < avg_pbr * 0.85:
                        sig = f"역사적 평균({avg_pbr:.2f}배) 대비 저평가 → BULLISH"
                    elif pbr > avg_pbr * 1.15:
                        sig = f"역사적 평균({avg_pbr:.2f}배) 대비 고평가 → BEARISH"
                    else:
                        sig = f"역사적 평균({avg_pbr:.2f}배) 수준 → NEUTRAL"
                    lines.append(f"  PBR: {pbr:.2f}배 — {sig}")
            elif pbr > 0:
                lines.append(f"  PBR: {pbr:.2f}배")

            if div > 0:
                sig = "고배당 → BULLISH" if div >= 3.0 else ("저배당 → NEUTRAL" if div < 1.0 else "보통 수준 → NEUTRAL")
                lines.append(f"  배당수익률: {div:.2f}% — {sig}")

            return "\n".join(lines) if len(lines) > 1 else "기본 투자지표 데이터 없음"

        except Exception as exc:
            logger.warning("기본 투자지표 조회 실패 %s: %s", symbol, exc)
            return "기본 투자지표 데이터 없음"

    # ------------------------------------------------------------------
    # 차트
    # ------------------------------------------------------------------

    def plot_kline_chart(
        self,
        df: pd.DataFrame,
        target_date: date,
        symbol: str,
        window: int = 30,
    ) -> str:
        """최근 `window`봉 캔들차트를 PNG로 저장하고 경로를 반환한다."""
        end_idx = df.index.searchsorted(pd.Timestamp(target_date), side="right")
        start_idx = max(0, end_idx - window)
        sliced = df.iloc[start_idx:end_idx]

        if sliced.empty:
            raise ValueError(f"No data to plot for {symbol} around {target_date}")

        path = self.chart_dir / f"kline_{symbol}_{target_date}.png"
        mpf.plot(
            sliced,
            type="candle",
            style="charles",
            title=f"{symbol} Kline ({target_date})",
            savefig=str(path),
            volume=True,
            tight_layout=True,
        )
        return str(path)

    def plot_trading_chart(
        self,
        df: pd.DataFrame,
        actions: List[TradeAction],
        target_date: date,
        symbol: str,
        window: int = 60,
    ) -> str:
        """가격선 + 매매 마커를 PNG로 저장하고 경로를 반환한다."""
        end_idx = df.index.searchsorted(pd.Timestamp(target_date), side="right")
        start_idx = max(0, end_idx - window)
        sliced = df.iloc[start_idx:end_idx]

        if sliced.empty:
            raise ValueError(f"No data to plot for {symbol} around {target_date}")

        # 매매 마커 생성
        apds = []
        buy_dates = [a.date for a in actions if a.action == "BUY"]
        sell_dates = [a.date for a in actions if a.action == "SELL"]

        if buy_dates:
            buy_prices = pd.Series(index=sliced.index, dtype=float)
            for d in buy_dates:
                ts = pd.Timestamp(d)
                if ts in sliced.index:
                    buy_prices[ts] = sliced.loc[ts, "Low"] * 0.99
            apds.append(mpf.make_addplot(buy_prices, type="scatter", markersize=100, marker="^", color="green"))

        if sell_dates:
            sell_prices = pd.Series(index=sliced.index, dtype=float)
            for d in sell_dates:
                ts = pd.Timestamp(d)
                if ts in sliced.index:
                    sell_prices[ts] = sliced.loc[ts, "High"] * 1.01
            apds.append(mpf.make_addplot(sell_prices, type="scatter", markersize=100, marker="v", color="red"))

        path = self.chart_dir / f"trading_{symbol}_{target_date}.png"
        kwargs: dict = dict(
            type="line",
            style="charles",
            title=f"{symbol} Trading ({target_date})",
            savefig=str(path),
            volume=True,
            tight_layout=True,
        )
        if apds:
            kwargs["addplot"] = apds

        mpf.plot(sliced, **kwargs)
        return str(path)


# ------------------------------------------------------------------
# 헬퍼
# ------------------------------------------------------------------

def _strip_html(text: str) -> str:
    """간단한 HTML 태그 제거."""
    import re
    return re.sub(r"<[^>]+>", "", text).strip()
