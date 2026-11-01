# retrieve_knowledge

Semantic search over the cross-agent knowledge store. Returns ranked knowledge results matching a natural-language query.

## What it does

1. Accepts a natural-language query and optional filters
2. Computes a vector embedding of the query
3. Performs cosine-similarity search against all stored knowledge entries
4. Returns the top-N results ranked by relevance score

## Tools Required

None -- this skill operates directly on the KnowledgeStore (in-process).

## Input

- `query` -- Natural language search query (required)
- `knowledge_type` -- Optional filter: one of `design_decision`, `component`, `failure`, `constraint`, `session` (the `knowledge.search` values)
- `top_k` -- Maximum results to return (default: 5, max: 50). `limit` is still accepted

## Output

- `results` -- List of KnowledgeResult objects, each containing:
  - `entry_id` -- UUID of the knowledge entry
  - `content` -- The knowledge text
  - `knowledge_type` -- Category
  - `source_path` -- The source it was ingested from, for citation
  - `score` -- Cosine similarity to the query, clamped to 0-1
  - `metadata` -- Additional key-value metadata
- `query` -- Echo of the original query
- `total_results` -- Count of results returned

## Limitations

- Needs an embedding service; without one the skill refuses rather than ranking by nothing
- Relevance depends on the quality of the embedding service
- The default local hash embedding is deterministic but not semantically meaningful
- In-memory store does not persist across restarts
