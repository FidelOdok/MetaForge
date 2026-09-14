import apiClient from '../client';
import type {
  KnowledgeType,
  SourceSummary,
  SourcesQuery,
  KnowledgeSearchResult,
  KnowledgeSearchQuery,
} from '../../types/knowledge';

/**
 * Wire-level response shape from ``GET /api/v1/knowledge/sources``.
 *
 * The gateway emits camelCase aliases per ``SourceSummaryResponse`` in
 * ``api_gateway/knowledge/routes.py``. We normalise to snake_case at the
 * boundary so the rest of the dashboard speaks the CLI / MCP layout.
 */
interface SourceSummaryRaw {
  sourcePath?: string;
  source_path?: string;
  knowledgeType?: string | null;
  knowledge_type?: string | null;
  fragmentCount?: number;
  fragment_count?: number;
  indexedAt?: string;
  indexed_at?: string;
  metadata?: Record<string, unknown>;
}

interface SourceListResponseRaw {
  sources: SourceSummaryRaw[];
  total: number;
}

const KNOWLEDGE_TYPES: ReadonlyArray<KnowledgeType> = [
  'design_decision',
  'component',
  'failure',
  'constraint',
  'session',
  'other',
];

function normaliseKnowledgeType(value: string | null | undefined): KnowledgeType | null {
  if (!value) return null;
  return (KNOWLEDGE_TYPES as ReadonlyArray<string>).includes(value)
    ? (value as KnowledgeType)
    : null;
}

function mapSource(raw: SourceSummaryRaw): SourceSummary {
  return {
    source_path: raw.sourcePath ?? raw.source_path ?? '',
    knowledge_type: normaliseKnowledgeType(raw.knowledgeType ?? raw.knowledge_type ?? null),
    fragment_count: raw.fragmentCount ?? raw.fragment_count ?? 0,
    indexed_at: raw.indexedAt ?? raw.indexed_at ?? '',
    metadata: raw.metadata ?? {},
  };
}

/**
 * Fetch the list of ingested knowledge sources.
 *
 * Backed by ``GET /api/v1/knowledge/sources`` (L1-C1, PR #174). The
 * server filters by ``knowledgeType`` and ``projectId`` when provided;
 * the dashboard exposes the same filter chips so users can narrow
 * client-side or push the filter to the server.
 */
export async function listSources(query: SourcesQuery = {}): Promise<SourceSummary[]> {
  const params: Record<string, string | number> = {};
  if (query.knowledge_type) params.knowledgeType = query.knowledge_type;
  if (query.project_id) params.projectId = query.project_id;
  if (query.limit !== undefined) params.limit = query.limit;
  if (query.offset !== undefined) params.offset = query.offset;

  const { data } = await apiClient.get<SourceListResponseRaw>('/knowledge/sources', {
    params,
  });
  return (data.sources ?? []).map(mapSource);
}

/** Wire-level entry shape from ``GET /api/v1/knowledge/search`` — camelCase per ``KnowledgeEntryResponse``. */
interface KnowledgeSearchEntryRaw {
  id: string;
  content: string;
  knowledgeType?: string | null;
  metadata?: Record<string, unknown>;
  sourcePath?: string | null;
  createdAt: string;
}

interface SearchResponseRaw {
  query: string;
  results: KnowledgeSearchEntryRaw[];
  totalFound: number;
}

function mapSearchResult(raw: KnowledgeSearchEntryRaw): KnowledgeSearchResult {
  return {
    id: raw.id,
    content: raw.content,
    knowledge_type: normaliseKnowledgeType(raw.knowledgeType ?? null),
    metadata: raw.metadata ?? {},
    source_path: raw.sourcePath ?? null,
    created_at: raw.createdAt,
  };
}

/**
 * Semantic search over the ingested knowledge corpus.
 *
 * Backed by ``GET /api/v1/knowledge/search`` (MET-390, project scoping
 * MET-670). Without `project_id`, the server falls back to the
 * `default` tenant rather than searching everything — so this must be
 * passed the same way `listSources` passes it, or a project-scoped
 * ingest is silently unsearchable from that project's own UI.
 */
export async function searchKnowledge(query: KnowledgeSearchQuery): Promise<KnowledgeSearchResult[]> {
  const params: Record<string, string | number> = { query: query.query };
  if (query.knowledge_type) params.knowledgeType = query.knowledge_type;
  if (query.project_id) params.projectId = query.project_id;
  if (query.limit !== undefined) params.limit = query.limit;

  const { data } = await apiClient.get<SearchResponseRaw>('/knowledge/search', { params });
  return (data.results ?? []).map(mapSearchResult);
}
