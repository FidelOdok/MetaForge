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
- `knowledge_type` -- Optional filter by category (e.g. "design_rule", "material_property")
- `limit` -- Maximum results to return (default: 5, max: 50)

## Output

- `results` -- List of KnowledgeResult objects, each containing:
  - `entry_id` -- UUID of the knowledge entry
  - `content` -- The knowledge text
  - `knowledge_type` -- Category
  - `source` -- Where this knowledge came from
  - `score` -- Relevance score (0-1, higher is better)
  - `metadata` -- Additional key-value metadata
- `query` -- Echo of the original query
- `total_results` -- Count of results returned

## Limitations

- Relevance depends on the quality of the embedding service
- The default local hash embedding is deterministic but not semantically meaningful
- In-memory store does not persist across restarts
