# Neo4j Graph Engine — Backend Swap & Migration Guide

## Overview

The Digital Twin graph engine uses a pluggable backend architecture. The `GraphEngine` ABC
defines 11 methods that every backend must implement. Two backends are available:

| Backend | Class | Use Case |
|---------|-------|----------|
| In-Memory | `InMemoryGraphEngine` | Development, testing, CI |
| Neo4j | `Neo4jGraphEngine` | Production, large graphs, Cypher queries |

Both backends are interchangeable via the `TwinAPI` facade. The swap is transparent to
all consumers (agents, orchestrator, gateway).

## Connection Configuration

The backend is selected via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `METAFORGE_GRAPH_BACKEND` | `memory` | Backend selector: `"memory"` or `"neo4j"` |
| `METAFORGE_NEO4J_URI` | `bolt://localhost:7687` | Neo4j Bolt URI |
| `METAFORGE_NEO4J_USER` | `neo4j` | Neo4j username |
| `METAFORGE_NEO4J_PASSWORD` | `password` | Neo4j password |

### Programmatic Usage

```python
# Option 1: Automatic from environment
api = await InMemoryTwinAPI.create_from_env()

# Option 2: Explicit Neo4j
from twin_core.neo4j_graph_engine import Neo4jGraphEngine

engine = Neo4jGraphEngine(
    uri="bolt://localhost:7687",
    user="neo4j",
    password="secret",
)
await engine.connect()
```

## Neo4j Schema Setup

On first connection, `Neo4jGraphEngine.connect()` automatically creates:

### Constraints

- **`node_id_unique`** — Ensures `Node.id` is unique across all nodes

### Indexes

- **`node_type_index`** — Index on `Node.node_type` for filtered list queries
- **`edge_type_index`** — Index on `EDGE.edge_type` for edge filtering

These are created with `IF NOT EXISTS` so they are idempotent across restarts.

### Node Labels

Every node gets the `:Node` label plus a type-specific label derived from `node_type`:

- `:Node:WorkProduct`
- `:Node:Constraint`
- `:Node:Component`
- `:Node:Agent`
- `:Node:Version`

### Edge Type

All edges use the `:EDGE` relationship type with an `edge_type` property that stores
the `EdgeType` enum value (e.g., `"depends_on"`, `"contains"`).

### Property Serialization

Neo4j properties must be primitives or lists of primitives. Complex fields are handled:

- `dict` fields are serialized to JSON strings on write and deserialized on read
- `list` fields are serialized to JSON strings on write and deserialized on read
- `UUID` fields are stored as strings
- `datetime` fields are stored as ISO 8601 strings

## Data Migration Strategy (In-Memory to Neo4j)

### Prerequisites

1. Neo4j 5.x+ instance running and accessible
2. `neo4j` Python package installed (`pip install metaforge[neo4j]`)

### Migration Steps

1. **Export from in-memory**: Use `list_nodes()` and `get_edges()` to extract all data
2. **Create Neo4j engine**: Initialize `Neo4jGraphEngine` and call `connect()`
3. **Import nodes first**: Call `add_node()` for each node (order does not matter)
4. **Import edges second**: Call `add_edge()` for each edge (nodes must exist first)
5. **Verify**: Compare node/edge counts between old and new backends

```python
async def migrate(source: InMemoryGraphEngine, target: Neo4jGraphEngine) -> None:
    """Migrate all data from in-memory to Neo4j."""
    # Export all nodes
    nodes = await source.list_nodes()
    for node in nodes:
        await target.add_node(node)

    # Export all edges (iterate over all nodes, get outgoing edges)
    for node in nodes:
        edges = await source.get_edges(node.id, direction="outgoing")
        for edge in edges:
            await target.add_edge(edge)
```

### Verification

After migration, verify counts match:

```python
source_nodes = await source.list_nodes()
target_nodes = await target.list_nodes()
assert len(source_nodes) == len(target_nodes)
```

## Rollback Plan

1. Set `METAFORGE_GRAPH_BACKEND=memory` to revert to in-memory
2. Restart the service — the in-memory engine starts fresh (empty graph)
3. If data persistence is needed, re-import from a snapshot or re-run the design pipeline

To export data from Neo4j before rollback:

```python
engine = Neo4jGraphEngine(...)
await engine.connect()
nodes = await engine.list_nodes()
edges = []
for node in nodes:
    edges.extend(await engine.get_edges(node.id, direction="outgoing"))
# Serialize nodes/edges to JSON for backup
```

## Performance Considerations

### In-Memory Backend
- O(1) node lookup by ID (dict-based)
- O(n) for list/filter operations
- No persistence — data lost on restart
- Best for: tests, small graphs (<10K nodes)

### Neo4j Backend
- O(log n) node lookup via B-tree index on `id`
- Constant-time relationship traversal via native graph storage
- Full ACID transactions
- Cypher query language for complex graph patterns
- Best for: production, large graphs, complex traversals

### Recommendations

- Always create indexes before bulk imports (handled automatically by `connect()`)
- Use `query_cypher()` for complex multi-hop queries instead of iterative `get_neighbors()`
- Batch operations when possible to reduce round-trips
- Monitor query performance via the `metaforge_neo4j_query_duration_seconds` histogram

## Observability

The Neo4j backend emits:

- **Structured logs** via `structlog` for all operations (connect, add, get, delete, etc.)
- **OTel spans** for every database operation with attributes:
  - `db.system: neo4j`
  - `db.operation: add_node|get_node|update_node|...`
  - `node.id`, `node.type`, `edge.type` as applicable
  - `neo4j.result_count` for list/query operations
- **Metrics** via `MetricsRegistry`:
  - `metaforge_neo4j_query_duration_seconds` — histogram of query latency
  - `metaforge_neo4j_query_total` — counter of queries by operation and status
  - `metaforge_neo4j_active_connections` — gauge of active connections
