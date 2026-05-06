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
    # 뉴스 (네이버 뉴스 RSS)
    # ------------------------------------------------------------------

    def get_news(
        self,
        symbol: str,
        stock_name: str,
        target_date: date,
        max_items: int = 10,
    ) -> List[NewsItem]:
        """네이버 뉴스 RSS에서 종목 관련 뉴스를 수집한다.

        Args:
            symbol: 티커 (로깅용, 실제 검색엔 미사용)
            stock_name: 한글 종목명 (예: "삼성전자")
            target_date: 기준일. ±7일 이내 뉴스만 반환
            max_items: 최대 반환 개수
        """
        query = quote(f"{stock_name} 주가")
        url = f"https://search.naver.com/search.naver?where=rss&query={query}"

        try:
            feed = feedparser.parse(url)
        except Exception as exc:
            logger.warning("Naver RSS fetch failed for %s: %s", stock_name, exc)
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

            # 기준일 ±7일 필터
            if abs((published.date() - target_date).days) > 7:
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
