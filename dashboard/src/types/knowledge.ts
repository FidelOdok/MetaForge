/**
 * Type definitions for the L1 knowledge corpus.
 *
 * Mirrors the wire shape of ``GET /api/v1/knowledge/sources`` exposed by
 * ``api_gateway/knowledge/routes.py:list_knowledge_sources`` (MET-411).
 * The gateway uses camelCase aliases (``sourcePath``, ``knowledgeType``,
 * ``fragmentCount``, ``indexedAt``); the dashboard surfaces snake_case to
 * match the CLI / MCP resource layout.
 */

/**
 * Canonical L1 knowledge categories — kept in sync with
 * ``digital_twin.knowledge.store.KnowledgeType`` plus an "other" bucket
 * for sources whose ``knowledge_type`` is null/absent.
 */
export type KnowledgeType =
  | 'design_decision'
  | 'component'
  | 'failure'
  | 'constraint'
  | 'session'
  | 'other';

/**
 * One row from the knowledge sources index.
 *
 * ``knowledge_type`` may be null when the source predates typed
 * ingestion. ``metadata`` is open-ended; the datasheet corpus
 * conventionally surfaces ``vendor`` and ``mpn`` keys for components.
 */
export interface SourceSummary {
  source_path: string;
  knowledge_type: KnowledgeType | null;
  fragment_count: number;
  indexed_at: string;
  metadata: Record<string, unknown>;
}

/** Filters supported by ``GET /api/v1/knowledge/sources``. */
export interface SourcesQuery {
  knowledge_type?: KnowledgeType;
  project_id?: string;
  limit?: number;
  offset?: number;
}

/**
 * One semantic search hit from ``GET /api/v1/knowledge/search``. Distinct
 * from `SourceSummary` — search returns individual matched chunks/entries,
 * not source-level rollups.
 */
export interface KnowledgeSearchResult {
  id: string;
  content: string;
  knowledge_type: KnowledgeType | null;
  metadata: Record<string, unknown>;
  source_path: string | null;
  created_at: string;
}

/** Params supported by ``GET /api/v1/knowledge/search``. */
export interface KnowledgeSearchQuery {
  query: string;
  knowledge_type?: KnowledgeType;
  project_id?: string;
  limit?: number;
}
