from pathlib import Path
from book_distiller.core import version


def test_source_version():
    assert version.get_version() == (Path(__file__).resolve().parents[2] / "VERSION").read_text().strip()


def test_installed_version(monkeypatch, tmp_path):
    monkeypatch.setattr(version, "__file__", str(tmp_path / "package/core/version.py"))
    monkeypatch.setattr(version, "version", lambda name: "0.1.0.dev0")
    assert version.get_version() == "0.1.0-dev"
