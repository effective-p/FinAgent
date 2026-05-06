"""Step 2 — MemoryStore 단위 테스트."""
from __future__ import annotations

import tempfile

import pytest

from finagent.memory.store import MemoryStore


def _store() -> MemoryStore:
    tmp = tempfile.mkdtemp()
    return MemoryStore(persist_dir=tmp)


class TestMemoryStore:
    def test_add_and_retrieve(self):
        store = _store()
        store.add(
            "market_intelligence",
            "삼성전자 주가 상승세 지속",
            {"symbol": "005930", "date": "2024-01-02"},
        )
        results = store.retrieve("market_intelligence", "삼성전자 주가", top_k=1)
        assert len(results) == 1
        assert "삼성전자" in results[0]

    def test_retrieve_empty_collection_returns_empty(self):
        store = _store()
        results = store.retrieve("market_intelligence", "anything", top_k=3)
        assert results == []

    def test_top_k_limits_results(self):
        store = _store()
        for i in range(5):
            store.add(
                "low_level_reflection",
                f"단기 가격 반등 신호 {i}",
                {"symbol": "005930", "date": f"2024-01-0{i+1}"},
            )
        results = store.retrieve("low_level_reflection", "가격 반등", top_k=2)
        assert len(results) <= 2

    def test_upsert_same_id_overwrites(self):
        store = _store()
        meta = {"symbol": "005930", "date": "2024-01-02"}
        store.add("market_intelligence", "원본 텍스트", meta)
        store.add("market_intelligence", "원본 텍스트", meta)  # same id
        assert store.count("market_intelligence") == 1

    def test_diversified_retrieve_deduplicates(self):
        store = _store()
        store.add(
            "market_intelligence",
            "반도체 업황 개선 기대감",
            {"symbol": "005930", "date": "2024-01-02"},
        )
        queries = ["반도체 업황", "반도체 업황"]  # 동일 쿼리 2개
        results = store.diversified_retrieve("market_intelligence", queries, top_k_each=1)
        assert len(results) == 1  # 중복 제거

    def test_diversified_retrieve_multiple_queries(self):
        store = _store()
        docs = [
            ("단기 급등 후 조정 가능성", "2024-01-02"),
            ("중기 상승 추세 유효", "2024-01-03"),
            ("장기 펀더멘털 견고", "2024-01-04"),
        ]
        for text, d in docs:
            store.add("market_intelligence", text, {"symbol": "005930", "date": d})

        queries = ["단기 급등", "중기 추세", "장기 펀더멘털"]
        results = store.diversified_retrieve("market_intelligence", queries, top_k_each=1)
        assert 1 <= len(results) <= 3

    def test_collections_are_independent(self):
        store = _store()
        store.add(
            "market_intelligence",
            "MI 요약 텍스트",
            {"symbol": "005930", "date": "2024-01-02"},
        )
        assert store.count("market_intelligence") == 1
        assert store.count("low_level_reflection") == 0
        assert store.count("high_level_reflection") == 0

    def test_invalid_collection_raises(self):
        store = _store()
        with pytest.raises(ValueError):
            store.add("nonexistent", "text", {"symbol": "X", "date": "2024-01-01"})

    def test_count(self):
        store = _store()
        assert store.count("high_level_reflection") == 0
        store.add(
            "high_level_reflection",
            "과거 매수 결정이 적절했음",
            {"symbol": "005930", "date": "2024-01-02"},
        )
        assert store.count("high_level_reflection") == 1

    def test_semantic_similarity_ranking(self):
        """의미적으로 유사한 문서가 더 높은 순위여야 한다."""
        store = _store()
        store.add("market_intelligence", "반도체 수출 호조로 주가 상승", {"symbol": "005930", "date": "2024-01-02"})
        store.add("market_intelligence", "날씨가 맑고 기온이 높습니다", {"symbol": "005930", "date": "2024-01-03"})
        results = store.retrieve("market_intelligence", "반도체 주가", top_k=2)
        assert "반도체" in results[0]
