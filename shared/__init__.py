"""Shared package — foundational types and utilities used across all modules."""

from shared.storage import FileStorageService, default_storage

__all__ = ["FileStorageService", "default_storage"]
