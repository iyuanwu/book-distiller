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


def pytest_addoption(parser):
    parser.addoption("--run-docling-real", action="store_true", default=False,
                     help="Run real Docling integration tests (PDF may download model weights).")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-docling-real"):
        skip = pytest.mark.skip(reason="Use --run-docling-real for actual Docling conversion")
        for item in items:
            if "docling_real" in item.keywords:
                item.add_marker(skip)
