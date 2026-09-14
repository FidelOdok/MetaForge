"""Standalone maintenance and migration scripts.

``scripts.migrations`` was already a package while ``scripts`` itself was not,
so mypy resolved ``scripts/migrations/backfill_project_id.py`` under two module
names at once (``migrations.*`` and ``scripts.migrations.*``) and refused to
check anything: "Source file found twice under different module names". The
file is imported and run as ``scripts.migrations.backfill_project_id``, so the
parent being a package is what matches the actual usage.
"""
