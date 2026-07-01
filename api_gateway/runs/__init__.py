"""OpenAI-compatible Runs API for the robust harness (MET-547, Phase 1).

Exposes the harness run lifecycle over REST: create a run, fetch/list runs, and
submit a human approval decision for a run paused at a gate. Endpoints live
under ``/v1/runs``. SSE streaming of run events is a follow-up slice.
"""
