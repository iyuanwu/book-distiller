"""Versioned source and manifest records, without book content models."""
from pathlib import Path, PurePosixPath
from typing import Literal, Self
from pydantic import AwareDatetime, Field, model_validator
from book_distiller.core.models import BookIdentity, CommonModel, EditionIdentity

SUPPORTED_EXTENSIONS = frozenset({".pdf", ".epub", ".txt", ".md", ".markdown", ".docx"})


class SourceFile(CommonModel):
    """Source provenance; copied paths are relative, references are absolute."""
    original_filename: str = Field(min_length=1)
    original_path: Path
    stored_path: str
    file_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    extension: str
    copy_mode: Literal["copy", "reference"]

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        if self.extension not in SUPPORTED_EXTENSIONS:
            raise ValueError("Unsupported source extension")
        if not self.original_path.is_absolute():
            raise ValueError("original_path must be absolute")
        if Path(self.original_filename).name != self.original_filename:
            raise ValueError("original_filename must be a filename")
        if self.copy_mode == "copy":
            if self.stored_path != f"source/original{self.extension}":
                raise ValueError("Copied sources must use source/original.<ext>")
        elif not Path(self.stored_path).is_absolute() or Path(self.stored_path) != self.original_path:
            raise ValueError("Referenced source must use the absolute original path")
        return self


class Manifest(CommonModel):
    """Portable per-edition inventory; contains no parsed content."""
    manifest_version: Literal["1.0"] = "1.0"
    book: BookIdentity
    edition: EditionIdentity
    source: SourceFile
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.edition.book_id != self.book.book_id:
            raise ValueError("Edition must belong to the manifest Book")
        slug = self.book.slug
        if not slug or slug.startswith(".") or PurePosixPath(slug).name != slug or "\\" in slug:
            raise ValueError("Manifest requires a safe, single-component book slug")
        if not self.edition.display_title:
            raise ValueError("Manifest requires an edition display_title")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        return self
