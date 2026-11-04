"""Cross-domain dependency and impact (FORGE-793, CDE slice 7).

``interfaces``: typed, revision-aware interface edges between items (XDM-001).
``cross_domain``: the impact walker over them, revision-scoped invalidation of
the results a change makes out of date, and the reruns it proposes
(XDM-002..007, CHG-001, CHG-004).

This is product dependency (which part, board, pin map, firmware module and
simulation depend on which), not the workflow-step DAG in
``orchestrator/dependency_engine.py``.
"""
