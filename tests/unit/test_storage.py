"""Unit tests for shared.storage — FileStorageService (MET-215)."""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.storage import FileStorageService


@pytest.fixture()
def storage(tmp_path: Path) -> FileStorageService:
    """Return a FileStorageService rooted in a temporary directory."""
    return FileStorageService(storage_root=str(tmp_path))


class TestSave:
    """Tests for FileStorageService.save."""

    def test_creates_file_with_correct_content(self, storage: FileStorageService) -> None:
        content = b"solid bracket\nendsolid bracket"
        path = storage.save("sess-1", "bracket.step", content)
        assert Path(path).read_bytes() == content

    def test_returns_deterministic_path_for_same_content(self, storage: FileStorageService) -> None:
        content = b"deterministic-content"
        path_a = storage.save("sess-1", "file.bin", content)
        path_b = storage.save("sess-1", "file.bin", content)
        assert path_a == path_b

    def test_different_content_produces_different_path(self, storage: FileStorageService) -> None:
        path_a = storage.save("sess-1", "file.bin", b"aaa")
        path_b = storage.save("sess-1", "file.bin", b"bbb")
        assert path_a != path_b

    def test_creates_session_subdirectory(self, storage: FileStorageService) -> None:
        storage.save("my-session", "out.dat", b"data")
        assert (storage.root / "my-session").is_dir()


class TestGet:
    """Tests for FileStorageService.get."""

    def test_retrieves_saved_content(self, storage: FileStorageService) -> None:
        content = b"mesh-data-xyz"
        path = storage.save("sess-1", "mesh.inp", content)
        assert storage.get(path) == content

    def test_raises_for_missing_file(self, storage: FileStorageService) -> None:
        with pytest.raises(FileNotFoundError):
            storage.get("/nonexistent/path/file.bin")


class TestListFiles:
    """Tests for FileStorageService.list_files."""

    def test_returns_all_files_for_session(self, storage: FileStorageService) -> None:
        storage.save("sess-1", "a.step", b"aaa")
        storage.save("sess-1", "b.mesh", b"bbb")
        files = storage.list_files("sess-1")
        assert len(files) == 2

    def test_returns_empty_for_unknown_session(self, storage: FileStorageService) -> None:
        assert storage.list_files("no-such-session") == []

    def test_does_not_mix_sessions(self, storage: FileStorageService) -> None:
        storage.save("sess-1", "a.step", b"aaa")
        storage.save("sess-2", "b.step", b"bbb")
        assert len(storage.list_files("sess-1")) == 1
        assert len(storage.list_files("sess-2")) == 1


class TestDelete:
    """Tests for FileStorageService.delete."""

    def test_deletes_existing_file(self, storage: FileStorageService) -> None:
        path = storage.save("sess-1", "tmp.bin", b"data")
        assert storage.delete(path) is True
        assert not Path(path).exists()

    def test_returns_false_for_missing_file(self, storage: FileStorageService) -> None:
        assert storage.delete("/nonexistent/file.bin") is False


class TestContentHash:
    """Tests for FileStorageService.content_hash."""

    def test_is_deterministic(self) -> None:
        data = b"hello world"
        assert FileStorageService.content_hash(data) == FileStorageService.content_hash(data)

    def test_different_content_different_hash(self) -> None:
        assert FileStorageService.content_hash(b"a") != FileStorageService.content_hash(b"b")

    def test_returns_64_char_hex_string(self) -> None:
        h = FileStorageService.content_hash(b"test")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


class TestConfiguration:
    """Tests for storage root configuration."""

    def test_custom_storage_root(self, tmp_path: Path) -> None:
        custom = tmp_path / "custom"
        svc = FileStorageService(storage_root=str(custom))
        assert svc.root == custom.resolve()

    def test_env_var_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env_dir = str(tmp_path / "from-env")
        monkeypatch.setenv("METAFORGE_STORAGE_ROOT", env_dir)
        svc = FileStorageService()
        assert svc.root == Path(env_dir).resolve()

    def test_explicit_root_takes_precedence_over_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("METAFORGE_STORAGE_ROOT", "/should/not/use")
        explicit = str(tmp_path / "explicit")
        svc = FileStorageService(storage_root=explicit)
        assert svc.root == Path(explicit).resolve()
