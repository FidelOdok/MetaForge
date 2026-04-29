"""Twin MCP tools — exposes the digital-twin graph as MCP tools (MET-382).

Five tools so external harnesses can triage structural questions to
the authoritative graph instead of routing every question to LightRAG:

- ``twin.get_node`` — fetch a single node + first-hop neighbours
- ``twin.thread_for`` — walk the digital thread from a root node
- ``twin.find_by_property`` — indexed property lookup
- ``twin.constraint_violations`` — current violations, severity-ordered
- ``twin.query_cypher`` — power-user escape hatch (read-only by default)
"""
