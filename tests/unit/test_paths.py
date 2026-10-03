import pytest
from book_distiller.core.paths import find_project_root
from book_distiller.core.errors import ConfigurationError


def test_nested_root(tmp_path):
    (tmp_path / "pyproject.toml").touch()
    (tmp_path / "VERSION").touch()
    nested = tmp_path / "book_distiller/core"
    nested.mkdir(parents=True)
    assert find_project_root(nested) == tmp_path


def test_missing_root(tmp_path):
    with pytest.raises(ConfigurationError):
        find_project_root(tmp_path)
