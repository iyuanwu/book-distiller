"""Project root discovery without machine-specific paths."""
from pathlib import Path
from book_distiller.core.errors import ConfigurationError


def find_project_root(start: Path | None = None) -> Path:
    """Find a checkout from an explicit location, cwd, or editable package."""
    origins = [start] if start is not None else [Path.cwd(), Path(__file__).resolve().parent]
    for origin in origins:
        origin = origin.resolve()
        for candidate in (origin, *origin.parents):
            if (candidate / "pyproject.toml").is_file() and (candidate / "book_distiller").is_dir() and (candidate / "VERSION").is_file():
                return candidate
    raise ConfigurationError("Project root not found. Run from the Book Distiller checkout.")


def storage_root(project_root: Path) -> Path:
    """Resolve optional isolated storage without changing the project root."""
    import os
    value = os.environ.get("BOOK_DISTILLER_HOME")
    return Path(value).expanduser().resolve() if value else project_root.resolve()
