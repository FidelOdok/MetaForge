"""LightRAG-backed implementation of ``KnowledgeService``.

ADR-008 picks LightRAG (HKUDS) as the L1 framework. This adapter is the
**only** module in the repo that imports ``lightrag``. All other callers
go through the ``KnowledgeService`` Protocol so swapping in a different
backend (LlamaIndex, R2R successor, etc.) needs no churn outside this
file.

Design decisions worth flagging:

* **Pre-chunking by markdown heading** — LightRAG ships its own chunker,
  but it does not surface per-chunk heading metadata. We split the
  source into heading-aware chunks before ``ainsert``, then feed each
  chunk as a separate document with the heading + chunk index baked
  into ``file_paths``. That round-trips citation metadata through
  search.
* **Naive vector mode** — we use ``QueryParam(mode="naive")`` so search
  is a pure pgvector cosine query. KG-extraction modes (``local``,
  ``global``, ``hybrid``) require an LLM; we keep them off the L1
  critical path until P1.13.
* **No-op LLM model func** — LightRAG's constructor demands an LLM
  func. Ours returns an empty string; KG extraction is therefore
  effectively disabled, which is fine for naive vector RAG.
* **Lazy LightRAG imports** — keeps unit tests (and any environment
  without ``lightrag-hku`` installed) able to import the module and
  satisfy the Protocol check.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import structlog

from digital_twin.knowledge.service import (
    IngestResult,
    KnowledgeService,
    SearchHit,
)
from digital_twin.knowledge.types import KnowledgeType
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.knowledge.lightrag_service")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


# all-MiniLM-L6-v2 dimension. ADR-008 fixes this for L1; switching the
# embedding model is a P1.13 toggle, not a runtime config.
_DEFAULT_EMBEDDING_DIM = 384
_DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"


@dataclass
class LightRAGConfig:
    """Pure-data config for ``LightRAGKnowledgeService``.

    Kept as a plain dataclass so callers (CLI, tests) can construct it
    without dragging Pydantic into the import path of every consumer.
    """

    working_dir: str = "./.lightrag-storage"
    embedding_model: str = _DEFAULT_EMBEDDING_MODEL
    embedding_dim: int = _DEFAULT_EMBEDDING_DIM
    # asyncpg DSN, e.g. postgresql://metaforge:metaforge@localhost:5432/metaforge
    postgres_dsn: str | None = None
    # When ``True``, LightRAG creates ``LIGHTRAG_*`` tables alongside the
    # legacy ``knowledge_entries`` table. Lets the spike share the dev
    # DB without colliding with ``PgVectorKnowledgeStore``.
    namespace_prefix: str = "lightrag"
    # Per-chunk character budget. Heading-aware chunking still applies
    # this as an upper bound to avoid 50KB chunks under a single H2.
    max_chunk_chars: int = 1500
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Heading-aware markdown chunking
# ---------------------------------------------------------------------------


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


@dataclass
class _Chunk:
    """Internal chunk with its parent heading and ordinal position."""

    text: str
    heading: str | None
    index: int
    total: int


def _chunk_by_heading(content: str, max_chars: int) -> list[_Chunk]:
    """Split markdown by H1..H6 boundaries, capping each chunk at ``max_chars``.

    Heading text is preserved as the chunk's ``heading`` field so search
    hits can show "Decision > Trade-offs" style breadcrumbs without
    re-parsing the source.
    """
    if not content.strip():
        return []

    matches = list(_HEADING_RE.finditer(content))
    raw: list[tuple[str | None, str]] = []
    if not matches:
        raw.append((None, content))
    else:
        # Pre-heading preamble.
        first = matches[0]
        if first.start() > 0:
            preamble = content[: first.start()].strip()
            if preamble:
                raw.append((None, preamble))
        for i, match in enumerate(matches):
            heading = match.group(2).strip()
            body_start = match.end()
            body_end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            body = content[body_start:body_end].strip()
            section = f"{match.group(0).strip()}\n\n{body}".strip()
            raw.append((heading, section))

    # Enforce max_chars by hard-splitting any oversized section.
    bounded: list[tuple[str | None, str]] = []
    for heading, section in raw:
        if len(section) <= max_chars:
            bounded.append((heading, section))
            continue
        for start in range(0, len(section), max_chars):
            bounded.append((heading, section[start : start + max_chars]))

    total = len(bounded)
    return [
        _Chunk(text=text, heading=heading, index=idx, total=total)
        for idx, (heading, text) in enumerate(bounded)
    ]


def _stable_chunk_id(source_path: str, index: int, text: str) -> str:
    """Deterministic chunk id so re-ingesting the same source dedupes.

    LightRAG's ``ainsert(ids=...)`` uses these as the document keys.
    """
    h = hashlib.sha256()
    h.update(source_path.encode("utf-8"))
    h.update(b"\x00")
    h.update(str(index).encode("utf-8"))
    h.update(b"\x00")
    h.update(text.encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


# Sentinel marker baked into ``file_paths`` so we can round-trip
# (source_path, chunk_index, total_chunks, heading, knowledge_type,
#  source_work_product_id) through LightRAG without a side-channel store.
_META_DELIM = "\x1f"  # ASCII unit separator — safe in file paths LightRAG echoes back.
_META_VERSION = "v1"


def _encode_meta(
    source_path: str,
    chunk_index: int,
    total_chunks: int,
    heading: str | None,
    knowledge_type: KnowledgeType,
    source_work_product_id: UUID | None,
    extra: dict[str, Any] | None,
) -> str:
    """Pack our citation metadata into the LightRAG ``file_paths`` slot.

    The PG ``lightrag_vdb_chunks.file_path`` column is a plain ``text``
    field, so a JSON blob round-trips losslessly. We bake in a
    ``"ver"`` field so future changes to the schema can be detected
    without breaking older rows.
    """
    import json

    payload = {
        "ver": _META_VERSION,
        "src": source_path,
        "ci": chunk_index,
        "tc": total_chunks,
        "h": heading,
        "kt": str(knowledge_type),
        "wp": str(source_work_product_id) if source_work_product_id else None,
        "x": extra or {},
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _decode_meta(file_path_field: str) -> dict[str, Any] | None:
    """Inverse of ``_encode_meta``.

    Returns ``None`` for legacy rows that didn't go through us so the
    caller can degrade to a citation-less hit instead of crashing.
    """
    import json

    if not file_path_field:
        return None
    if _META_DELIM in file_path_field:
        _, file_path_field = file_path_field.rsplit(_META_DELIM, 1)
    try:
        data = json.loads(file_path_field)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("ver") != _META_VERSION:
        return None
    return data


class LightRAGKnowledgeService:
    """LightRAG-backed ``KnowledgeService`` implementation.

    Construction is pure config — no I/O. Call ``initialize()`` once
    before the first ingest/search.
    """

    def __init__(
        self,
        working_dir: str = "./.lightrag-storage",
        *,
        postgres_dsn: str | None = None,
        embedding_model: str = _DEFAULT_EMBEDDING_MODEL,
        embedding_dim: int = _DEFAULT_EMBEDDING_DIM,
        namespace_prefix: str = "lightrag",
        max_chunk_chars: int = 1500,
        config: LightRAGConfig | None = None,
    ) -> None:
        self._cfg = config or LightRAGConfig(
            working_dir=working_dir,
            embedding_model=embedding_model,
            embedding_dim=embedding_dim,
            postgres_dsn=postgres_dsn,
            namespace_prefix=namespace_prefix,
            max_chunk_chars=max_chunk_chars,
        )
        self._rag: Any = None
        self._embedder: Any = None
        self._initialized = False
        # source_path -> set of LightRAG doc ids for delete_by_source.
        self._source_index: dict[str, set[str]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Set up the LightRAG instance + pgvector backends.

        Called once at gateway boot. Idempotent.
        """
        if self._initialized:
            return

        with tracer.start_as_current_span("lightrag.initialize") as span:
            span.set_attribute("lightrag.working_dir", self._cfg.working_dir)
            span.set_attribute("lightrag.embedding_model", self._cfg.embedding_model)
            try:
                from lightrag import LightRAG  # type: ignore[import-not-found]
                from lightrag.kg.shared_storage import (  # type: ignore[import-not-found]
                    initialize_pipeline_status,
                )
                from lightrag.utils import EmbeddingFunc  # type: ignore[import-not-found]
            except ImportError as exc:
                logger.error("lightrag_not_installed", error=str(exc))
                raise RuntimeError(
                    "lightrag-hku is not installed. Install with: pip install lightrag-hku"
                ) from exc

            os.makedirs(self._cfg.working_dir, exist_ok=True)

            # Configure pgvector via env vars LightRAG reads at storage init time.
            if self._cfg.postgres_dsn:
                self._apply_postgres_env(self._cfg.postgres_dsn)

            embedding_func = EmbeddingFunc(
                embedding_dim=self._cfg.embedding_dim,
                max_token_size=8192,
                func=self._make_embedder(),
            )

            kwargs: dict[str, Any] = {
                "working_dir": self._cfg.working_dir,
                "embedding_func": embedding_func,
                "llm_model_func": _noop_llm_model_func,
                # LightRAG 1.4 uses ``workspace`` as the namespace key.
                "workspace": self._cfg.namespace_prefix,
            }
            if self._cfg.postgres_dsn:
                kwargs.update(
                    vector_storage="PGVectorStorage",
                    kv_storage="PGKVStorage",
                    doc_status_storage="PGDocStatusStorage",
                    # Keep ``graph_storage`` as the default in-memory
                    # NetworkXStorage. PGGraphStorage requires Apache
                    # AGE on the same Postgres instance — orthogonal to
                    # naive vector RAG and a heavy infrastructure
                    # dependency we don't need at L1.
                )
            self._rag = LightRAG(**kwargs)
            await self._rag.initialize_storages()
            await initialize_pipeline_status()
            # Pre-warm the sentence-transformers model so the first
            # ainsert call doesn't pay the full model-load latency
            # inside LightRAG's 60 s embedding-worker timeout.
            await self._prewarm_embedder()
            self._initialized = True
            logger.info(
                "lightrag_initialized",
                working_dir=self._cfg.working_dir,
                embedding_dim=self._cfg.embedding_dim,
                postgres=bool(self._cfg.postgres_dsn),
            )

    async def close(self) -> None:
        """Best-effort teardown of LightRAG storages.

        LightRAG exposes ``finalize_storages`` in newer releases; we
        guard for older versions that lack it.
        """
        if self._rag is None:
            return
        finalize = getattr(self._rag, "finalize_storages", None)
        if finalize is not None:
            try:
                await finalize()
            except Exception as exc:  # pragma: no cover — best effort
                logger.warning("lightrag_finalize_failed", error=str(exc))
        self._initialized = False

    # ------------------------------------------------------------------
    # KnowledgeService Protocol
    # ------------------------------------------------------------------

    async def ingest(
        self,
        content: str,
        source_path: str,
        knowledge_type: KnowledgeType,
        source_work_product_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> IngestResult:
        await self._ensure_initialized()
        with tracer.start_as_current_span("lightrag.ingest") as span:
            span.set_attribute("knowledge.source_path", source_path)
            span.set_attribute("knowledge.type", str(knowledge_type))

            if not content or not content.strip():
                logger.info("lightrag_ingest_empty", source_path=source_path)
                raise ValueError("content is empty or whitespace")

            chunks = _chunk_by_heading(content, self._cfg.max_chunk_chars)
            if not chunks:
                logger.info("lightrag_ingest_empty", source_path=source_path)
                raise ValueError("content produced zero chunks after parsing")

            ids = [_stable_chunk_id(source_path, c.index, c.text) for c in chunks]
            file_paths = [
                _encode_meta(
                    source_path=source_path,
                    chunk_index=c.index,
                    total_chunks=c.total,
                    heading=c.heading,
                    knowledge_type=knowledge_type,
                    source_work_product_id=source_work_product_id,
                    extra=metadata,
                )
                for c in chunks
            ]
            texts = [c.text for c in chunks]

            # Pre-delete prior chunks indexed under the same source_path
            # so re-ingest with new content doesn't leave orphans. Also
            # emits the ``knowledge_consumer_predelete`` event the L1
            # observability contract (MET-307) promises.
            existing_ids = self._source_index.get(source_path, set())
            stale_ids = existing_ids - set(ids)
            if stale_ids:
                deleted = await self.delete_by_source(source_path)
                logger.info(
                    "knowledge_consumer_predelete",
                    source_path=source_path,
                    deleted=deleted,
                )

            await self._rag.ainsert(input=texts, ids=ids, file_paths=file_paths)

            self._source_index.setdefault(source_path, set()).update(ids)
            entry_ids = [_uuid_from_chunk_id(cid) for cid in ids]
            span.set_attribute("knowledge.chunks_indexed", len(chunks))
            logger.info(
                "lightrag_ingested",
                source_path=source_path,
                chunks=len(chunks),
                knowledge_type=str(knowledge_type),
            )
            return IngestResult(
                entry_ids=entry_ids,
                chunks_indexed=len(chunks),
                source_path=source_path,
            )

    async def search(
        self,
        query: str,
        top_k: int = 5,
        knowledge_type: KnowledgeType | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        """Pure vector search.

        We bypass ``aquery``/``aquery_data`` because they don't return
        per-chunk similarity scores in 1.4.x and pull in KG / rerank /
        token-budget logic that L1 doesn't need.

        For PG storage we run a direct cosine query that includes the
        ``1 - distance`` similarity, since LightRAG's PG ``chunks`` SQL
        template drops the score column. For NanoVectorDB we call
        ``chunks_vdb.query`` and read ``distance`` directly.
        """
        await self._ensure_initialized()
        with tracer.start_as_current_span("lightrag.search") as span:
            span.set_attribute("knowledge.query_length", len(query))
            span.set_attribute("knowledge.top_k", top_k)

            chunks_vdb = getattr(self._rag, "chunks_vdb", None)
            if chunks_vdb is None:
                raise RuntimeError("LightRAG instance has no chunks_vdb storage.")
            fetch_k = top_k * 4 if (knowledge_type or filters) else top_k

            if self._cfg.postgres_dsn:
                raw_chunks = await self._search_pg(chunks_vdb, query, fetch_k)
            else:
                raw_chunks = await chunks_vdb.query(query, top_k=fetch_k)

            hits: list[SearchHit] = []
            for chunk in raw_chunks or []:
                hit = self._chunk_to_hit(chunk)
                if hit is None:
                    continue
                if knowledge_type is not None and hit.knowledge_type != knowledge_type:
                    continue
                if filters and not _matches_filters(hit, filters):
                    continue
                hits.append(hit)

            hits.sort(key=lambda h: h.similarity_score, reverse=True)
            hits = hits[:top_k]
            span.set_attribute("knowledge.result_count", len(hits))
            return hits

    async def _search_pg(self, chunks_vdb: Any, query: str, top_k: int) -> list[dict[str, Any]]:
        """Cosine query straight to ``lightrag_vdb_chunks`` for real scores.

        LightRAG's PG SQL template returns id/content/file_path but not
        the cosine distance. We re-issue the query through asyncpg with
        the distance projected so callers see meaningful similarity
        scores instead of a row of 0.0s.
        """
        embedder = self._make_embedder()
        emb = await embedder([query])
        vec = emb[0] if hasattr(emb, "__len__") else emb
        embedding_str = "[" + ",".join(str(float(v)) for v in vec) + "]"
        table = getattr(chunks_vdb, "table_name", "lightrag_vdb_chunks")
        workspace = getattr(chunks_vdb, "workspace", self._cfg.namespace_prefix)
        threshold = 1 - getattr(chunks_vdb, "cosine_better_than_threshold", 0.0)
        sql = (
            f"SELECT c.id, c.content, c.file_path, "
            f"       1 - (c.content_vector <=> $2::vector) AS similarity "
            f"FROM {table} c "
            f"WHERE c.workspace = $1 "
            f"  AND c.content_vector <=> $2::vector < $3 "
            f"ORDER BY c.content_vector <=> $2::vector "
            f"LIMIT $4;"
        )

        import asyncpg  # type: ignore[import-untyped]

        assert self._cfg.postgres_dsn is not None
        conn = await asyncpg.connect(self._cfg.postgres_dsn)
        try:
            rows = await conn.fetch(sql, workspace, embedding_str, threshold, top_k)
        finally:
            await conn.close()
        return [dict(row) for row in rows]

    async def delete_by_source(self, source_path: str) -> int:
        await self._ensure_initialized()
        ids = self._source_index.get(source_path, set())
        if not ids:
            return 0
        deleted = 0
        for chunk_id in list(ids):
            try:
                # LightRAG exposes ``adelete_by_doc_id`` in 1.4.x.
                await self._rag.adelete_by_doc_id(chunk_id)
                deleted += 1
            except AttributeError:
                # Fallback for older LightRAG: drop the chunk from
                # storage directly.
                vec_store = getattr(self._rag, "chunks_vdb", None)
                if vec_store is not None and hasattr(vec_store, "delete"):
                    await vec_store.delete([chunk_id])
                    deleted += 1
            except Exception as exc:  # pragma: no cover — best effort
                logger.warning("lightrag_delete_failed", chunk_id=chunk_id, error=str(exc))
        self._source_index.pop(source_path, None)
        logger.info("lightrag_deleted_source", source_path=source_path, deleted=deleted)
        return deleted

    async def health_check(self) -> dict[str, Any]:
        if not self._initialized:
            return {
                "status": "uninitialized",
                "backend": "lightrag",
                "pgvector": False,
            }
        pgvector_ok = False
        if self._cfg.postgres_dsn:
            try:
                import asyncpg  # type: ignore[import-untyped]

                conn = await asyncpg.connect(self._cfg.postgres_dsn)
                try:
                    row = await conn.fetchval("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
                    pgvector_ok = row == 1
                finally:
                    await conn.close()
            except Exception as exc:
                logger.warning("lightrag_health_pg_failed", error=str(exc))
        return {
            "status": "ok",
            "backend": "lightrag",
            "pgvector": pgvector_ok,
            "embedding_model": self._cfg.embedding_model,
            "embedding_dim": self._cfg.embedding_dim,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _ensure_initialized(self) -> None:
        if not self._initialized:
            await self.initialize()

    async def _prewarm_embedder(self) -> None:
        """Force the sentence-transformers model to load eagerly.

        ``SentenceTransformer.__init__`` is fast but the first
        ``.encode()`` call blocks on lazy weight loading and tokenizer
        warm-up — typically 30-90 s on a cold filesystem. LightRAG's
        embedding worker only allows 60 s before raising
        ``TimeoutError``. Eagerly running a 1-token encode here keeps
        the first user-facing ``ingest`` call fast and well under the
        worker budget.
        """
        try:
            embedder = self._make_embedder()
            await embedder(["warmup"])
            logger.info("lightrag_embedder_prewarmed", model=self._cfg.embedding_model)
        except Exception as exc:  # pragma: no cover — best effort
            logger.warning("lightrag_embedder_prewarm_failed", error=str(exc))

    def _make_embedder(self) -> Any:
        """Return an async embedding callable for LightRAG.

        Wraps ``sentence-transformers`` synchronously inside
        ``asyncio.to_thread`` so the gateway event loop doesn't block.
        """

        async def _embed(texts: list[str]) -> Any:
            import numpy as np  # type: ignore[import-untyped]

            model = self._get_embedder()
            vectors = await asyncio.to_thread(
                model.encode, texts, convert_to_numpy=True, show_progress_bar=False
            )
            return np.asarray(vectors, dtype=np.float32)

        return _embed

    def _get_embedder(self) -> Any:
        if self._embedder is not None:
            return self._embedder
        try:
            from sentence_transformers import (  # type: ignore[import-untyped]
                SentenceTransformer,
            )
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is required for the LightRAG adapter. "
                "Install with: pip install sentence-transformers"
            ) from exc
        self._embedder = SentenceTransformer(self._cfg.embedding_model)
        return self._embedder

    @staticmethod
    def _apply_postgres_env(dsn: str) -> None:
        """Translate an asyncpg DSN to the env vars LightRAG expects.

        LightRAG's PG storages read POSTGRES_HOST / POSTGRES_PORT /
        POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DATABASE. We only
        set keys that are unset so caller env wins.
        """
        from urllib.parse import urlparse

        parsed = urlparse(dsn)
        env_map = {
            "POSTGRES_HOST": parsed.hostname or "localhost",
            "POSTGRES_PORT": str(parsed.port or 5432),
            "POSTGRES_USER": parsed.username or "",
            "POSTGRES_PASSWORD": parsed.password or "",
            "POSTGRES_DATABASE": (parsed.path or "/").lstrip("/") or "postgres",
        }
        for key, value in env_map.items():
            os.environ.setdefault(key, value)

    async def _naive_search_via_aquery(self, query: str, top_k: int) -> list[dict[str, Any]]:
        """Fallback path for LightRAG releases without ``aget_context_chunks``."""
        from lightrag import QueryParam  # type: ignore[import-not-found]

        param = QueryParam(mode="naive", top_k=top_k, only_need_context=True)
        ctx = await self._rag.aquery(query, param=param)
        return _parse_naive_context(ctx)

    @staticmethod
    def _chunk_to_hit(chunk: Any) -> SearchHit | None:
        """Translate a LightRAG context chunk into a ``SearchHit``.

        Field names vary by storage backend:

        * ``_search_pg`` returns ``id, content, file_path, similarity``
        * ``NanoVectorDBStorage.query`` returns ``content, file_path,
          distance`` (lower is better)
        """
        if isinstance(chunk, dict):
            content = chunk.get("content") or chunk.get("text") or ""
            score: float
            if chunk.get("similarity") is not None:
                score = float(chunk["similarity"])
            elif chunk.get("similarity_score") is not None:
                score = float(chunk["similarity_score"])
            elif chunk.get("score") is not None:
                score = float(chunk["score"])
            elif chunk.get("distance") is not None:
                # NanoVectorDB returns cosine distance — convert to
                # similarity. Distance is 1 - cosine_similarity.
                score = max(0.0, 1.0 - float(chunk["distance"]))
            else:
                score = 0.0
            file_path_field = chunk.get("file_path") or chunk.get("file_paths") or ""
            if isinstance(file_path_field, list):
                file_path_field = file_path_field[0] if file_path_field else ""
        else:
            content = getattr(chunk, "content", "") or ""
            score = float(getattr(chunk, "similarity_score", 0.0) or 0.0)
            file_path_field = getattr(chunk, "file_path", "") or ""
        meta = _decode_meta(file_path_field) or {}
        if not content:
            return None
        wp_raw = meta.get("wp")
        wp_id: UUID | None = None
        if wp_raw:
            try:
                wp_id = UUID(wp_raw)
            except (ValueError, TypeError):
                wp_id = None
        kt_raw = meta.get("kt")
        kt: KnowledgeType | None = None
        if kt_raw:
            try:
                kt = KnowledgeType(kt_raw)
            except ValueError:
                kt = None
        return SearchHit(
            content=content,
            similarity_score=score,
            source_path=meta.get("src"),
            heading=meta.get("h"),
            chunk_index=meta.get("ci"),
            total_chunks=meta.get("tc"),
            metadata=meta.get("x") or {},
            knowledge_type=kt,
            source_work_product_id=wp_id,
        )


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------


async def _noop_llm_model_func(  # pragma: no cover — signature must match LightRAG's
    prompt: str,
    system_prompt: str | None = None,
    history_messages: list[dict[str, str]] | None = None,
    **kwargs: Any,
) -> str:
    """Stand-in LLM for naive vector mode.

    LightRAG's constructor demands ``llm_model_func``; in mode="naive"
    it is never called. Returning an empty string keeps KG extraction
    a no-op if a code path ever reaches it.
    """
    return ""


def _uuid_from_chunk_id(chunk_id: str) -> UUID:
    """Stable UUIDv5-ish projection of a hex chunk id, for ``IngestResult.entry_ids``."""
    try:
        return UUID(hex=chunk_id[:32])
    except ValueError:
        return uuid4()


def _matches_filters(hit: SearchHit, filters: dict[str, Any]) -> bool:
    for key, expected in filters.items():
        if key == "source_work_product_id":
            if str(hit.source_work_product_id) != str(expected):
                return False
        elif key == "source_path":
            if hit.source_path != expected:
                return False
        else:
            actual = hit.metadata.get(key)
            if actual != expected:
                return False
    return True


def _parse_naive_context(ctx: Any) -> list[dict[str, Any]]:
    """Best-effort parse of LightRAG's naive-mode context payload.

    LightRAG returns a markdown-ish blob in older releases. We extract
    chunk dicts when JSON is available; otherwise return an empty list
    and let the caller fall back to ``aget_context_chunks`` once
    available.
    """
    import json

    if isinstance(ctx, list):
        return [c for c in ctx if isinstance(c, dict)]
    if isinstance(ctx, dict):
        chunks = ctx.get("chunks") or ctx.get("results") or []
        return [c for c in chunks if isinstance(c, dict)]
    if isinstance(ctx, str):
        try:
            data = json.loads(ctx)
        except json.JSONDecodeError:
            return []
        return _parse_naive_context(data)
    return []


# Verify the adapter satisfies the Protocol at import time so mistakes
# fail loudly during unit collection rather than at first runtime call.
_protocol_check: KnowledgeService = LightRAGKnowledgeService.__new__(LightRAGKnowledgeService)
del _protocol_check
