# ingest_knowledge

Ingest text content into the cross-agent knowledge store with automatic chunking and embedding.

## What it does

1. Accepts text content with a knowledge type and source
2. Splits long content into overlapping chunks for better retrieval
3. Computes vector embeddings for each chunk
4. Stores all chunks in the knowledge store

## Tools Required

None -- this skill operates directly on the KnowledgeStore (in-process).

## Input

- `content` -- The text content to ingest (required)
- `knowledge_type` -- Category of this knowledge (required, e.g. "design_rule", "material_property")
- `source` -- Origin of this knowledge (required, e.g. "datasheet:LM7805", "standard:IPC-2221")
- `metadata` -- Optional additional key-value metadata

## Output

- `entry_id` -- UUID of the primary knowledge entry created
- `embedded` -- Whether the content was successfully embedded
- `chunk_count` -- Number of chunks created (1 for short content)
- `content_length` -- Total length of ingested content

## Limitations

- Chunking uses a fixed character-based window, not sentence-aware splitting
- The default local hash embedding is deterministic but not semantically meaningful
- In-memory store does not persist across restarts
