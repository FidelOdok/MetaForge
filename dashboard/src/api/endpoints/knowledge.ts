import apiClient from '../client';
import type { KnowledgeType, SourceSummary, SourcesQuery } from '../../types/knowledge';

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
