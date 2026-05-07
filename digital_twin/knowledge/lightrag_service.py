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

from digital_twin.knowledge.chunker import chunk_csv
from digital_twin.knowledge.service import (
    IngestResult,
    KnowledgeService,
    SearchHit,
    SourceSummary,
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
# PDF detection + extraction (MET-399)
# ---------------------------------------------------------------------------


# CLI ``cli/forge_cli/ingest.py:_read_file_content`` ships PDFs as a
# latin-1-decoded string so the bytes survive a JSON round-trip. The
# magic bytes ``%PDF-`` are pure ASCII, so they survive that round-trip
# unchanged — letting us sniff PDFs at this layer without a side-channel
# content-type field.
_PDF_MAGIC = "%PDF-"


def _looks_like_pdf(content: str) -> bool:
    """Return True when ``content`` is a latin-1-decoded PDF payload."""
    return content.startswith(_PDF_MAGIC)


# ---------------------------------------------------------------------------
# CSV detection (MET-340)
# ---------------------------------------------------------------------------


def _looks_like_csv(source_path: str, metadata: dict[str, Any] | None) -> bool:
    """Return True when this payload should go through the CSV chunker.

    Two triggers, per spec:

    * ``metadata.content_type == "text/csv"`` — explicit signal from
      callers that set the MIME type (gateway, tests, future CLI work).
    * ``source_path`` ends in ``.csv`` (case-insensitive) — the
      practical primary trigger from today's CLI, which doesn't set
      ``content_type`` for CSVs (see ``cli/forge_cli/ingest.py:_read_file_content``).
    """
    if metadata and metadata.get("content_type") == "text/csv":
        return True
    return source_path.lower().endswith(".csv")


def _extract_pdf_text(pdf_bytes: bytes) -> tuple[str, int]:
    """Extract text from PDF bytes via pdfplumber, returning (text, pages).

    Each page is rendered as a ``## Page N`` H2 section so the existing
    heading-aware chunker can split on page boundaries and bake the
    page label into the chunk's ``heading`` field. The format matches
    ``scripts/datasheets/fetch_and_extract.py`` so the offline fixture
    pipeline and the live ingest path produce identical chunk shapes.

    Long-term home: ``raganything`` (declared in the ``[knowledge]``
    extra) is the spec'd PDF/multimodal parser. We use pdfplumber here
    because it's already pulled in via ``[dev]`` (and is a transitive
    of raganything via pdfminer.six). When raganything's container
    integration lands, this function moves there and the ingest branch
    below collapses to a single ``raganything.parse()`` call.
    """
    import io

    import pdfplumber

    pages: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            # Match scripts/datasheets/fetch_and_extract.py exactly so a
            # PDF ingested live and one extracted offline produce the
            # same chunk shapes downstream.
            pages.append(f"\n\n## Page {i}\n\n{text}\n")
    return "".join(pages), len(pages)


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


def _hash_content(content: str | bytes) -> str:
    """SHA-256 hex digest of the raw ingest payload.

    Drives the MET-307 supersede decision: if the engineer edited the
    file, the hash changes and the prior chunks must be retired before
    we store new ones. Strings are encoded as UTF-8 (the wire format we
    take in over JSON-RPC); bytes are hashed as-is so PDFs — which
    enter as a latin-1-decoded ``str`` containing the raw bytes — get a
    stable digest regardless of which branch handles them.
    """
    if isinstance(content, bytes):
        return hashlib.sha256(content).hexdigest()
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


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
        reranker_enabled: bool = False,
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
        # MET-307: source_path -> last-stored content_sha256. Used to
        # short-circuit identical re-ingests in-process and as a
        # fast-path before falling back to the PG SELECT for the
        # cross-process case. Persisted copy lives in chunk metadata
        # under ``metadata.content_sha256``.
        self._content_sha_index: dict[str, str] = {}
        # MET-335: hybrid-search reranker. The flag here sets the
        # default policy when ``search(rerank=...)`` is not explicitly
        # supplied; gateway boot reads ``KNOWLEDGE_RERANKER_ENABLED``
        # and threads it in. The reranker itself is constructed lazily
        # on first use so disabled deployments never load ~440 MB of
        # cross-encoder weights.
        self._reranker_enabled = reranker_enabled
        self._reranker: Any = None

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
        project_id: UUID | None = None,
    ) -> IngestResult:
        """Ingest a document.

        ``project_id`` (MET-401) is stamped into ``metadata["project_id"]``
        so subsequent searches can scope to it. The MCP adapter forwards
        the active call-context project_id; callers may also pass it
        explicitly. An explicit ``project_id`` argument wins over any
        existing ``metadata["project_id"]`` so the multi-tenant isolation
        contract is unambiguous at the storage layer.
        """
        await self._ensure_initialized()
        with tracer.start_as_current_span("lightrag.ingest") as span:
            span.set_attribute("knowledge.source_path", source_path)
            span.set_attribute("knowledge.type", str(knowledge_type))
            if project_id is not None:
                span.set_attribute("knowledge.project_id", str(project_id))

            if not content or not content.strip():
                logger.info(
                    "lightrag_ingest_empty",
                    source_path=source_path,
                    project_id=str(project_id) if project_id is not None else None,
                )
                raise ValueError("content is empty or whitespace")

            # MET-401: stamp project_id into the chunk metadata so
            # `search(project_id=...)` can scope correctly. Explicit
            # argument always wins over any pre-existing metadata key.
            metadata = dict(metadata or {})
            if project_id is not None:
                metadata["project_id"] = str(project_id)

            # MET-307: hash the raw content so we can detect edits to
            # the same source_path. Two outcomes:
            #   * matching prior hash -> identical re-ingest, dedup
            #     (return chunks_indexed=0) without re-chunking.
            #   * different hash (and prior chunks exist) -> the
            #     engineer edited the file: predelete the stale
            #     fragments, emit ``knowledge_consumer_predelete``, then
            #     proceed with normal chunking + storage.
            content_sha256 = _hash_content(content)
            metadata["content_sha256"] = content_sha256
            existing_sha = await self._existing_content_sha256(source_path)
            if existing_sha is not None and existing_sha == content_sha256:
                logger.info(
                    "lightrag_ingest_dedup",
                    source_path=source_path,
                    content_sha256=content_sha256,
                    project_id=str(project_id) if project_id is not None else None,
                )
                return IngestResult(
                    entry_ids=[],
                    chunks_indexed=0,
                    source_path=source_path,
                )
            if existing_sha is not None and existing_sha != content_sha256:
                old_chunk_count = len(self._source_index.get(source_path, set()))
                deleted = await self.delete_by_source(source_path)
                if deleted > old_chunk_count:
                    old_chunk_count = deleted
                span.set_attribute("knowledge.supersede", True)
                logger.info(
                    "knowledge_consumer_predelete",
                    source_path=source_path,
                    old_chunk_count=old_chunk_count,
                )

            # MET-399: detect PDF payloads (latin-1-decoded bytes whose
            # magic bytes survive the round-trip) and parse them through
            # pdfplumber before chunking. The result is markdown-shaped
            # text with ``## Page N`` H2 sections so the existing
            # heading-aware chunker handles it identically to a normal
            # markdown document. raganything is the long-term home —
            # see ``_extract_pdf_text``.
            if _looks_like_pdf(content):
                pdf_bytes = content.encode("latin-1")
                extracted_text, page_count = _extract_pdf_text(pdf_bytes)
                span.set_attribute("knowledge.pdf_pages", page_count)
                logger.info(
                    "pdf_extracted",
                    source_path=source_path,
                    pages=page_count,
                    total_chars=len(extracted_text),
                )
                content = extracted_text
                chunks = _chunk_by_heading(content, self._cfg.max_chunk_chars)
                per_chunk_extras: list[dict[str, Any]] = [dict(metadata) for _ in chunks]
            elif _looks_like_csv(source_path, metadata):
                # MET-340: row-level CSV chunking. Each data row becomes
                # its own ``_Chunk`` carrying the row's MPN/columns in
                # ``content`` and the row's structured metadata
                # (row_index, columns, header) in the per-chunk extra
                # dict so downstream consumers can hit a BOM by part
                # number.
                csv_chunks = chunk_csv(content)
                total = len(csv_chunks)
                chunks = [
                    _Chunk(text=row.content, heading=None, index=row.row_index, total=total)
                    for row in csv_chunks
                ]
                per_chunk_extras = [
                    {
                        **metadata,
                        "row_index": row.row_index,
                        "columns": row.columns,
                        "header": row.header,
                    }
                    for row in csv_chunks
                ]
                span.set_attribute("knowledge.csv_rows", total)
                logger.info(
                    "csv_chunked",
                    source_path=source_path,
                    rows=total,
                    columns=len(csv_chunks[0].header) if csv_chunks else 0,
                )
            else:
                chunks = _chunk_by_heading(content, self._cfg.max_chunk_chars)
                per_chunk_extras = [dict(metadata) for _ in chunks]

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
                    extra=per_chunk_extras[i],
                )
                for i, c in enumerate(chunks)
            ]
            texts = [c.text for c in chunks]

            await self._rag.ainsert(input=texts, ids=ids, file_paths=file_paths)

            self._source_index.setdefault(source_path, set()).update(ids)
            self._content_sha_index[source_path] = content_sha256
            entry_ids = [_uuid_from_chunk_id(cid) for cid in ids]
            span.set_attribute("knowledge.chunks_indexed", len(chunks))
            logger.info(
                "lightrag_ingested",
                source_path=source_path,
                chunks=len(chunks),
                knowledge_type=str(knowledge_type),
                project_id=str(project_id) if project_id is not None else None,
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
        project_id: UUID | None = None,
        rerank: bool = False,
    ) -> list[SearchHit]:
        """Vector search with optional cross-encoder reranking.

        We bypass ``aquery``/``aquery_data`` because they don't return
        per-chunk similarity scores in 1.4.x and pull in KG / rerank /
        token-budget logic that L1 doesn't need.

        For PG storage we run a direct cosine query that includes the
        ``1 - distance`` similarity, since LightRAG's PG ``chunks`` SQL
        template drops the score column. For NanoVectorDB we call
        ``chunks_vdb.query`` and read ``distance`` directly.

        ``project_id`` (MET-401) scopes the search to chunks ingested
        with that ``project_id`` stamped into their metadata. When
        ``project_id is None`` we fall back to the documented
        default-tenant behaviour: only chunks whose
        ``metadata.project_id == "default"`` are returned. This is the
        safer default than "search every project" — leaking project A's
        docs into an unscoped search would defeat the whole point of
        the isolation contract. Cross-project admin queries are an
        explicit out-of-band concern (the MCP adapter does not expose
        a way to bypass scoping).

        ``rerank`` (MET-335) controls hybrid-search reranking. When
        true we fetch ``top_k * 3`` candidates from the vector store,
        run them through a ``BAAI/bge-reranker-base`` cross-encoder,
        and truncate to ``top_k``. The reranker is lazily constructed
        on first use; a deployment with ``rerank=False`` never
        instantiates the cross-encoder model. The reranker model load
        is ~440 MB so callers should not flip this on per-call without
        considering startup cost on the first request.
        """
        await self._ensure_initialized()
        with tracer.start_as_current_span("lightrag.search") as span:
            span.set_attribute("knowledge.query_length", len(query))
            span.set_attribute("knowledge.top_k", top_k)
            span.set_attribute("knowledge.rerank", bool(rerank))

            # MET-401: resolve the effective project scope.
            # - explicit ``project_id`` argument always wins
            # - otherwise scope to the "default" tenant for safety
            #   (do NOT silently search across all projects)
            scope_project_id: str = str(project_id) if project_id is not None else "default"
            span.set_attribute("knowledge.project_id", scope_project_id)
            filters = dict(filters or {})
            filters.setdefault("project_id", scope_project_id)

            chunks_vdb = getattr(self._rag, "chunks_vdb", None)
            if chunks_vdb is None:
                raise RuntimeError("LightRAG instance has no chunks_vdb storage.")
            # MET-335: when reranking we widen the candidate pool so the
            # cross-encoder has more chunks to choose from before we
            # truncate to top_k. The 3x multiplier matches the spec.
            base_fetch_k = top_k * 4 if (knowledge_type or filters) else top_k
            fetch_k = max(base_fetch_k, top_k * 3) if rerank else base_fetch_k

            if self._cfg.postgres_dsn:
                raw_chunks = await self._search_pg(
                    chunks_vdb, query, fetch_k, project_scope=scope_project_id
                )
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

            if rerank and hits:
                reranker = self._get_reranker()
                hits = await reranker.rerank(query, hits)

            hits = hits[:top_k]
            span.set_attribute("knowledge.result_count", len(hits))
            logger.info(
                "lightrag_search",
                query_length=len(query),
                top_k=top_k,
                result_count=len(hits),
                project_id=scope_project_id,
                rerank=bool(rerank),
            )
            return hits

    async def _search_pg(
        self,
        chunks_vdb: Any,
        query: str,
        top_k: int,
        *,
        project_scope: str | None = None,
    ) -> list[dict[str, Any]]:
        """Cosine query straight to ``lightrag_vdb_chunks`` for real scores.

        LightRAG's PG SQL template returns id/content/file_path but not
        the cosine distance. We re-issue the query through asyncpg with
        the distance projected so callers see meaningful similarity
        scores instead of a row of 0.0s.

        When ``project_scope`` is set (MET-401) we push the project_id
        predicate down into SQL via the ``file_path`` JSON blob (our
        encoded metadata lives at ``$.x.project_id``). Pushing it into
        the LIMIT'd query is required for correctness: if we only
        post-filter, a tenant with many chunks could starve another
        tenant's results out of the top-k.
        """
        embedder = self._make_embedder()
        emb = await embedder([query])
        vec = emb[0] if hasattr(emb, "__len__") else emb
        embedding_str = "[" + ",".join(str(float(v)) for v in vec) + "]"
        table = getattr(chunks_vdb, "table_name", "lightrag_vdb_chunks")
        workspace = getattr(chunks_vdb, "workspace", self._cfg.namespace_prefix)
        threshold = 1 - getattr(chunks_vdb, "cosine_better_than_threshold", 0.0)

        params: list[Any] = [workspace, embedding_str, threshold, top_k]
        project_clause = ""
        if project_scope is not None:
            params.append(project_scope)
            # ``file_path`` is plain text but always carries our JSON
            # metadata blob (see ``_encode_meta``). Cast on read so the
            # filter runs without requiring a schema migration.
            project_clause = (
                "  AND COALESCE((c.file_path::jsonb->'x'->>'project_id'), 'default') = $5\n"
            )

        sql = (
            f"SELECT c.id, c.content, c.file_path, "
            f"       1 - (c.content_vector <=> $2::vector) AS similarity "
            f"FROM {table} c "
            f"WHERE c.workspace = $1 "
            f"  AND c.content_vector <=> $2::vector < $3 "
            f"{project_clause}"
            f"ORDER BY c.content_vector <=> $2::vector "
            f"LIMIT $4;"
        )

        import asyncpg  # type: ignore[import-untyped]

        assert self._cfg.postgres_dsn is not None
        conn = await asyncpg.connect(self._cfg.postgres_dsn)
        try:
            rows = await conn.fetch(sql, *params)
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
        # MET-307: clear the cached content hash so the next ingest
        # at this source treats it as a fresh insert, not a dedup.
        self._content_sha_index.pop(source_path, None)
        logger.info("lightrag_deleted_source", source_path=source_path, deleted=deleted)
        return deleted

    async def list_sources(
        self,
        project_id: UUID | None = None,
        knowledge_type: KnowledgeType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SourceSummary]:
        """Aggregate ingested chunks into one row per (source_path, type).

        Surfaces what the legacy ``KnowledgeStore.list()`` did but at the
        source granularity callers actually want — the
        ``metaforge://knowledge/sources`` MCP resource (L1-B1) and the
        ``forge sources list/show/delete`` CLI (L1-C1) both project this
        directly.

        ``project_id is None`` falls back to the documented "default
        tenant only" behaviour pinned in L1-A1: we scope to
        ``metadata.project_id == "default"`` rather than returning rows
        across every tenant. Cross-tenant admin listings are an explicit
        out-of-band concern; this method is the safe-by-default surface.

        ``knowledge_type`` filters by stored type (e.g. ``COMPONENT``).

        ``limit`` / ``offset`` paginate over the aggregated rows in
        ``indexed_at DESC`` order (most recently ingested source first).

        Two execution paths:

        * **Postgres** — runs a single GROUP-BY query against
          ``lightrag_vdb_chunks`` extracting ``src``, ``kt``, ``x`` from
          the JSON in ``file_path``. Connection acquisition mirrors
          ``_search_pg``.
        * **In-memory / NanoVectorDB** — falls back to the
          ``_source_index`` we keep on the service instance (the same
          structure ``delete_by_source`` uses). Lets unit tests exercise
          the public API without spinning up a real Postgres.
        """
        await self._ensure_initialized()
        with tracer.start_as_current_span("knowledge.list_sources") as span:
            scope_project_id: str = str(project_id) if project_id is not None else "default"
            kt_value = str(knowledge_type) if knowledge_type is not None else None
            span.set_attribute("knowledge.project_id", scope_project_id)
            if kt_value is not None:
                span.set_attribute("knowledge.type", kt_value)
            span.set_attribute("knowledge.limit", limit)
            span.set_attribute("knowledge.offset", offset)

            if self._cfg.postgres_dsn:
                summaries = await self._list_sources_pg(
                    project_scope=scope_project_id,
                    knowledge_type=kt_value,
                    limit=limit,
                    offset=offset,
                )
            else:
                summaries = self._list_sources_in_memory(
                    project_scope=scope_project_id,
                    knowledge_type=kt_value,
                    limit=limit,
                    offset=offset,
                )

            span.set_attribute("knowledge.result_count", len(summaries))
            logger.info(
                "knowledge_list_sources",
                project_id=scope_project_id,
                knowledge_type=kt_value,
                result_count=len(summaries),
                limit=limit,
                offset=offset,
            )
            return summaries

    async def _list_sources_pg(
        self,
        *,
        project_scope: str,
        knowledge_type: str | None,
        limit: int,
        offset: int,
    ) -> list[SourceSummary]:
        """GROUP BY against ``lightrag_vdb_chunks`` for the PG path.

        Why this shape:
        * source_path lives at ``file_path::jsonb->>'src'``
        * knowledge_type lives at ``file_path::jsonb->>'kt'``
        * project_id lives at ``file_path::jsonb->'x'->>'project_id'``
        * ``x`` (user metadata) lives at ``file_path::jsonb->'x'``

        We aggregate ``COUNT(*)`` for fragment_count, ``MAX(create_time)``
        for indexed_at, and pluck the first ``x`` blob via
        ``(array_agg(...))[1]`` so callers see at least one round-tripped
        metadata snapshot per source. Connection acquisition mirrors
        ``_search_pg``: a one-shot ``asyncpg.connect`` (we don't run a
        long-lived pool).
        """
        chunks_vdb = getattr(self._rag, "chunks_vdb", None)
        if chunks_vdb is None:
            return []
        table = getattr(chunks_vdb, "table_name", "lightrag_vdb_chunks")
        workspace = getattr(chunks_vdb, "workspace", self._cfg.namespace_prefix)

        params: list[Any] = [workspace, project_scope]
        kt_clause = ""
        if knowledge_type is not None:
            params.append(knowledge_type)
            kt_clause = "  AND c.file_path::jsonb->>'kt' = $3\n"
            limit_param_idx = 4
            offset_param_idx = 5
        else:
            limit_param_idx = 3
            offset_param_idx = 4
        params.append(limit)
        params.append(offset)

        sql = (
            f"SELECT c.file_path::jsonb->>'src' AS source_path, "
            f"       c.file_path::jsonb->>'kt' AS knowledge_type, "
            f"       COUNT(*) AS fragment_count, "
            f"       MAX(c.create_time) AS indexed_at, "
            f"       (array_agg(c.file_path::jsonb->'x'))[1] AS metadata "
            f"FROM {table} c "
            f"WHERE c.workspace = $1 "
            f"  AND COALESCE((c.file_path::jsonb->'x'->>'project_id'), 'default') = $2 "
            f"{kt_clause}"
            f"  AND c.file_path::jsonb->>'src' IS NOT NULL "
            f"GROUP BY source_path, knowledge_type "
            f"ORDER BY indexed_at DESC "
            f"LIMIT ${limit_param_idx} OFFSET ${offset_param_idx};"
        )

        import asyncpg  # type: ignore[import-untyped]

        assert self._cfg.postgres_dsn is not None
        conn = await asyncpg.connect(self._cfg.postgres_dsn)
        try:
            rows = await conn.fetch(sql, *params)
        finally:
            await conn.close()

        summaries: list[SourceSummary] = []
        for row in rows:
            kt_raw = row["knowledge_type"]
            kt: KnowledgeType | str | None
            if kt_raw is None:
                kt = None
            else:
                try:
                    kt = KnowledgeType(kt_raw)
                except ValueError:
                    kt = kt_raw
            metadata_raw = row["metadata"]
            metadata: dict[str, Any]
            if metadata_raw is None:
                metadata = {}
            elif isinstance(metadata_raw, str):
                import json

                try:
                    parsed = json.loads(metadata_raw)
                except json.JSONDecodeError:
                    parsed = {}
                metadata = parsed if isinstance(parsed, dict) else {}
            elif isinstance(metadata_raw, dict):
                metadata = metadata_raw
            else:
                metadata = {}
            summaries.append(
                SourceSummary(
                    source_path=row["source_path"],
                    knowledge_type=kt,
                    fragment_count=int(row["fragment_count"]),
                    indexed_at=row["indexed_at"],
                    metadata=metadata,
                )
            )
        return summaries

    def _list_sources_in_memory(
        self,
        *,
        project_scope: str,
        knowledge_type: str | None,
        limit: int,
        offset: int,
    ) -> list[SourceSummary]:
        """Fallback path when no Postgres DSN is configured.

        Reads the in-process state we already maintain for
        ``delete_by_source`` (``_source_index``) plus whatever metadata
        the underlying NanoVectorDB ``client_storage`` retained, so
        deployments running LightRAG on its default JSON storage still
        get a non-empty answer. Mirrors the shape of
        ``_naive_search_via_aquery`` — keep the API consistent across
        both backends.
        """
        chunks_vdb = getattr(self._rag, "chunks_vdb", None)
        if chunks_vdb is None:
            return []
        # NanoVectorDBStorage exposes ``client_storage`` as a dict with
        # ``"data": list[chunk_dict]``. We tolerate any shape that
        # round-trips ``id`` and our encoded ``file_path``.
        raw_chunks: list[dict[str, Any]] = []
        client_storage = getattr(chunks_vdb, "client_storage", None)
        if isinstance(client_storage, dict):
            data = client_storage.get("data") or []
            if isinstance(data, list):
                raw_chunks = [c for c in data if isinstance(c, dict)]
        # Group: (source_path, knowledge_type) -> aggregate.
        from datetime import UTC
        from datetime import datetime as _dt

        groups: dict[tuple[str, str | None], dict[str, Any]] = {}
        for chunk in raw_chunks:
            file_path_field = chunk.get("file_path") or chunk.get("file_paths") or ""
            if isinstance(file_path_field, list):
                file_path_field = file_path_field[0] if file_path_field else ""
            meta = _decode_meta(file_path_field)
            if not meta:
                continue
            src = meta.get("src")
            if not isinstance(src, str) or not src:
                continue
            user_meta = meta.get("x") or {}
            chunk_project = user_meta.get("project_id", "default")
            if str(chunk_project) != project_scope:
                continue
            kt_raw = meta.get("kt")
            if knowledge_type is not None and kt_raw != knowledge_type:
                continue
            indexed_raw = (
                chunk.get("create_time") or chunk.get("indexed_at") or chunk.get("update_time")
            )
            if isinstance(indexed_raw, _dt):
                indexed = indexed_raw
            elif isinstance(indexed_raw, (int, float)):
                indexed = _dt.fromtimestamp(float(indexed_raw), tz=UTC)
            else:
                indexed = _dt.now(UTC)
            key = (src, kt_raw)
            existing = groups.get(key)
            if existing is None:
                groups[key] = {
                    "fragment_count": 1,
                    "indexed_at": indexed,
                    "metadata": dict(user_meta),
                    "knowledge_type": kt_raw,
                }
            else:
                existing["fragment_count"] += 1
                if indexed > existing["indexed_at"]:
                    existing["indexed_at"] = indexed

        summaries: list[SourceSummary] = []
        for (src, kt_raw), agg in groups.items():
            kt: KnowledgeType | str | None
            if kt_raw is None:
                kt = None
            else:
                try:
                    kt = KnowledgeType(kt_raw)
                except ValueError:
                    kt = kt_raw
            summaries.append(
                SourceSummary(
                    source_path=src,
                    knowledge_type=kt,
                    fragment_count=int(agg["fragment_count"]),
                    indexed_at=agg["indexed_at"],
                    metadata=agg["metadata"],
                )
            )
        summaries.sort(key=lambda s: s.indexed_at, reverse=True)
        return summaries[offset : offset + limit]

    async def _existing_content_sha256(self, source_path: str) -> str | None:
        """Return the stored ``content_sha256`` for ``source_path`` (or None).

        Drives MET-307: identical re-ingests dedup, edited re-ingests
        supersede. Two lookup tiers:

        1. **In-process cache** (``self._content_sha_index``) — covers
           the common case of repeat ingests within one gateway
           lifetime. Fast and avoids a round-trip.
        2. **Postgres SELECT** — falls back to one row from the
           ``lightrag_vdb_chunks`` table when PG is configured, so the
           supersede decision survives gateway restarts. The column we
           read is ``file_path`` (plain text holding our JSON metadata
           blob); the hash is at ``$.x.content_sha256``. ``LIMIT 1`` —
           we only need one chunk's metadata to know the source's
           current hash.

        Returns ``None`` when no prior entry exists, or when the prior
        entry pre-dates MET-307 (no ``content_sha256`` stamped).
        """
        cached = self._content_sha_index.get(source_path)
        if cached is not None:
            return cached
        if not self._cfg.postgres_dsn:
            return None
        chunks_vdb = getattr(self._rag, "chunks_vdb", None)
        if chunks_vdb is None:
            return None
        table = getattr(chunks_vdb, "table_name", "lightrag_vdb_chunks")
        workspace = getattr(chunks_vdb, "workspace", self._cfg.namespace_prefix)
        sql = (
            f"SELECT c.file_path::jsonb->'x'->>'content_sha256' AS sha "
            f"FROM {table} c "
            f"WHERE c.workspace = $1 "
            f"  AND c.file_path::jsonb->>'src' = $2 "
            f"LIMIT 1;"
        )
        try:
            import asyncpg  # type: ignore[import-untyped]

            assert self._cfg.postgres_dsn is not None
            conn = await asyncpg.connect(self._cfg.postgres_dsn)
            try:
                row = await conn.fetchrow(sql, workspace, source_path)
            finally:
                await conn.close()
        except Exception as exc:  # pragma: no cover — best effort
            logger.warning(
                "lightrag_existing_sha_lookup_failed",
                source_path=source_path,
                error=str(exc),
            )
            return None
        if row is None:
            return None
        sha = row["sha"]
        if isinstance(sha, str) and sha:
            # Repopulate the cache so subsequent calls in this process
            # skip the SELECT.
            self._content_sha_index[source_path] = sha
            return sha
        return None

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

    def _get_reranker(self) -> Any:
        """Lazily construct the cross-encoder reranker (MET-335).

        Imported inside the method so the ``digital_twin.knowledge.reranker``
        module — which transitively reaches ``sentence_transformers``
        only on first ``rerank()`` — is not imported at search time when
        rerank is disabled. The instance is cached for subsequent calls.
        """
        if self._reranker is None:
            from digital_twin.knowledge.reranker import Reranker

            self._reranker = Reranker()
        return self._reranker

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
        elif key == "project_id":
            # MET-401: legacy chunks ingested before project isolation
            # have no project_id stamped — treat them as the "default"
            # tenant so an unscoped (project_id is None -> "default")
            # query still returns them. Mirrors the SQL COALESCE in
            # ``_search_pg``.
            actual = hit.metadata.get("project_id", "default")
            if str(actual) != str(expected):
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
