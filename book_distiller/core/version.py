"""Read the source version or installed distribution metadata."""
from importlib.metadata import version
from pathlib import Path


def get_version() -> str:
    """Return the display version, including outside an editable checkout."""
    source = Path(__file__).resolve().parents[2] / "VERSION"
    if source.is_file():
        return source.read_text(encoding="utf-8").strip()
    installed = version("book-distiller")
    # Packaging normalizes the initial development release to PEP 440.
    return installed.removesuffix(".dev0") + "-dev" if installed.endswith(".dev0") else installed
