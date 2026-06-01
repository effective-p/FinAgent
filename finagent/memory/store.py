from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List

from sentence_transformers import SentenceTransformer

from web.db import get_conn

logger = logging.getLogger(__name__)

_COLLECTIONS = ("market_intelligence", "low_level_reflection", "high_level_reflection")
_EMBEDDING_MODEL = "all-MiniLM-L6-v2"

_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(_EMBEDDING_MODEL)
    return _model


def _embed(text: str) -> list[float]:
    return _get_model().encode(text).tolist()


class MemoryStore:
    """pgvector 기반 3-컬렉션 메모리 저장소.

    run_id 로 실행 간 메모리를 완전히 격리한다.
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id

    def add(self, collection: str, text: str, metadata: Dict[str, Any]) -> None:
        self._validate(collection)
        doc_id = _make_id(self.run_id, metadata, text)
        vec = _embed(text)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO memory (id, run_id, collection, document, metadata, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE
                        SET document = EXCLUDED.document,
                            metadata = EXCLUDED.metadata,
                            embedding = EXCLUDED.embedding
                    """,
                    (doc_id, self.run_id, collection, text,
                     json.dumps(metadata, ensure_ascii=False),
                     str(vec)),  # pgvector accepts '[0.1, 0.2, ...]' string
                )
        logger.debug("MemoryStore.add [%s] run=%s id=%s", collection, self.run_id[:8], doc_id)

    def retrieve(self, collection: str, query_text: str, top_k: int = 3) -> List[str]:
        self._validate(collection)
        vec = _embed(query_text)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT document
                    FROM memory
                    WHERE run_id = %s AND collection = %s
                    ORDER BY embedding <=> %s
                    LIMIT %s
                    """,
                    (self.run_id, collection, str(vec), top_k),
                )
                rows = cur.fetchall()
        docs = [r[0] for r in rows]
        logger.debug("MemoryStore.retrieve [%s] query='%s' -> %d docs", collection, query_text[:40], len(docs))
        return docs

    def diversified_retrieve(
        self, collection: str, queries: List[str], top_k_each: int = 2
    ) -> List[str]:
        seen: set[str] = set()
        docs: List[str] = []
        for q in queries:
            for doc in self.retrieve(collection, q, top_k=top_k_each):
                if doc not in seen:
                    seen.add(doc)
                    docs.append(doc)
        logger.debug(
            "MemoryStore.diversified_retrieve [%s] %d queries -> %d unique docs",
            collection, len(queries), len(docs),
        )
        return docs

    def count(self, collection: str) -> int:
        self._validate(collection)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM memory WHERE run_id = %s AND collection = %s",
                    (self.run_id, collection),
                )
                return cur.fetchone()[0]

    def _validate(self, name: str) -> None:
        if name not in _COLLECTIONS:
            raise ValueError(f"Unknown collection '{name}'. Valid: {_COLLECTIONS}")


def _make_id(run_id: str, metadata: Dict[str, Any], text: str) -> str:
    # run_id 접두로 cross-run PK 충돌 방지 (동일 종목·날짜·유사 텍스트가 다른 run에서 발생할 때
    # ON CONFLICT DO UPDATE 가 다른 run 의 데이터를 덮어쓰는 문제 해결)
    run_prefix = str(run_id)[:8]
    symbol = str(metadata.get("symbol", "unknown"))
    date_str = str(metadata.get("date", "unknown"))
    text_hash = hashlib.sha1(text.encode()).hexdigest()[:8]
    return f"{run_prefix}_{symbol}_{date_str}_{text_hash}"
