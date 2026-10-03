"""All tests use isolated storage, never the user's Library or database."""
from pathlib import Path
import shutil
import pytest


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    root = tmp_path / "home"
    for name in ("library", "inbox", "backups"):
        (root / name).mkdir(parents=True)
    monkeypatch.setenv("BOOK_DISTILLER_HOME", str(root))
    return root


@pytest.fixture
def sample_source(tmp_path):
    destination = tmp_path / "原创 样本 (一).md"
    shutil.copyfile(Path(__file__).parent / "fixtures/sample.md", destination)
    return destination
