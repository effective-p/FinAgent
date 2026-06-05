from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

logger = logging.getLogger(__name__)

_COLLECTIONS = ("market_intelligence", "low_level_reflection", "high_level_reflection")


class MemoryStore:
    """ChromaDB 기반 3-컬렉션 메모리 저장소.

    각 컬렉션은 독립적으로 저장/조회하며,
    diversified_retrieve 로 여러 쿼리를 합산한 결과를 중복 없이 반환한다.
    """

    def __init__(
        self,
        persist_dir: str = "memory_db",
        embedding_model: str = "all-MiniLM-L6-v2",
    ) -> None:
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._ef = SentenceTransformerEmbeddingFunction(model_name=embedding_model)
        self._cols: Dict[str, chromadb.Collection] = {
            name: self._client.get_or_create_collection(
                name=name,
                embedding_function=self._ef,
            )
            for name in _COLLECTIONS
        }

    # ------------------------------------------------------------------
    # 저장
    # ------------------------------------------------------------------

    def add(
        self,
        collection: str,
        text: str,
        metadata: Dict[str, Any],
    ) -> None:
        """text를 임베딩해서 collection에 저장한다.

        metadata 필수 키: "date" (ISO str), "symbol" (str)
        같은 (symbol, date) 조합은 덮어쓴다(upsert).
        """
        self._validate_collection(collection)
        doc_id = _make_id(metadata, text)
        self._cols[collection].upsert(
            documents=[text],
            metadatas=[metadata],
            ids=[doc_id],
        )
        logger.debug("MemoryStore.add [%s] id=%s", collection, doc_id)

    # ------------------------------------------------------------------
    # 단순 조회
    # ------------------------------------------------------------------

    def retrieve(
        self,
        collection: str,
        query_text: str,
        top_k: int = 3,
    ) -> List[str]:
        """query_text와 가장 유사한 문서 최대 top_k 개를 반환한다."""
        self._validate_collection(collection)
        col = self._cols[collection]
        count = col.count()
        if count == 0:
            return []

        results = col.query(
            query_texts=[query_text],
            n_results=min(top_k, count),
        )
        docs: List[str] = results["documents"][0]
        logger.debug("MemoryStore.retrieve [%s] query='%s' → %d docs", collection, query_text[:40], len(docs))
        return docs

    # ------------------------------------------------------------------
    # Diversified Retrieval
    # ------------------------------------------------------------------

    def diversified_retrieve(
        self,
        collection: str,
        queries: List[str],
        top_k_each: int = 2,
    ) -> List[str]:
        """여러 쿼리로 독립 검색한 뒤 중복을 제거해 합친다.

        최대 len(queries) * top_k_each 개의 다양한 과거 기억을 반환한다.
        """
        seen: set[str] = set()
        docs: List[str] = []
        for q in queries:
            for doc in self.retrieve(collection, q, top_k=top_k_each):
                if doc not in seen:
                    seen.add(doc)
                    docs.append(doc)
        logger.debug(
            "MemoryStore.diversified_retrieve [%s] %d queries → %d unique docs",
            collection, len(queries), len(docs),
        )
        return docs

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    def _validate_collection(self, name: str) -> None:
        if name not in _COLLECTIONS:
            raise ValueError(f"Unknown collection '{name}'. Valid: {_COLLECTIONS}")

    def count(self, collection: str) -> int:
        self._validate_collection(collection)
        return self._cols[collection].count()


# ------------------------------------------------------------------
# 모듈 수준 헬퍼
# ------------------------------------------------------------------

def _make_id(metadata: Dict[str, Any], text: str) -> str:
    """(symbol, date, text_hash) 조합으로 결정적 ID를 생성한다."""
    symbol = str(metadata.get("symbol", "unknown"))
    date_str = str(metadata.get("date", "unknown"))
    text_hash = hashlib.sha1(text.encode()).hexdigest()[:8]
    return f"{symbol}_{date_str}_{text_hash}"
