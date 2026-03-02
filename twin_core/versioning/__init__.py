"""Versioning subsystem — Git-like branching, merging, and diffing for the Digital Twin."""

from twin_core.versioning.branch import InMemoryVersionEngine, VersionEngine
from twin_core.versioning.merge import MergeConflict

__all__ = [
    "VersionEngine",
    "InMemoryVersionEngine",
    "MergeConflict",
]
