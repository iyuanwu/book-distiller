"""All Docling-specific imports and structural mapping live in this adapter."""
from collections.abc import Iterator
from datetime import datetime, timezone
from importlib.metadata import version
import json
import tempfile
from pathlib import Path
from book_distiller.core.errors import PipelineError
from book_distiller.core.models.normalized import BlockType, BoundingBox, ParserMetadata
from book_distiller.parsers.base import ParserItem, ParserResult, ParserSpan


def docling_version() -> str:
    """Check the import and supported major version, without constructing a pipeline."""
    import docling.document_converter  # noqa: F401
    installed = version("docling")
    if installed.split(".")[0] != "2":
        raise PipelineError(f"Unsupported Docling version: {installed}; install Docling 2.x.")
    return installed


class DoclingAdapter:
    name = "docling"
    configuration = {"do_ocr": False, "do_table_structure": True, "enable_remote_services": False}

    def __init__(self) -> None:
        self.version = docling_version()

    def parse(self, source: Path, raw_directory: Path, source_sha256: str) -> ParserResult:
        """Use the public DocumentConverter/export APIs and retain raw output."""
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        extension = source.suffix.lower()
        formats = {".pdf": InputFormat.PDF, ".docx": InputFormat.DOCX, ".md": InputFormat.MD, ".markdown": InputFormat.MD}
        if hasattr(InputFormat, "EPUB"):
            formats[".epub"] = InputFormat.EPUB
        if extension not in formats:
            raise PipelineError(f"Docling {self.version} does not support source format {extension} in this adapter.")
        started = datetime.now(timezone.utc)
        options = PdfPipelineOptions(**self.configuration)
        converter = DocumentConverter(allowed_formats=[formats[extension]], format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
        # Path input avoids loading the complete source into a BytesIO buffer.
        # A private alias normalizes .markdown/uppercase extensions without copying bytes.
        with tempfile.TemporaryDirectory(prefix=".input-", dir=raw_directory) as temporary:
            alias = Path(temporary) / ("source" + (".md" if extension == ".markdown" else extension))
            alias.symlink_to(source.resolve())
            conversion = converter.convert(alias, raises_on_error=False)
        document = conversion.document
        status = conversion.status.value
        errors = [str(error.error_message) for error in conversion.errors]
        if status not in {"success", "partial_success"}:
            raise PipelineError(f"Docling conversion {status}: {'; '.join(errors) or 'No document produced'}")
        with (raw_directory / "docling.json").open("w", encoding="utf-8") as stream:
            json.dump(document.export_to_dict(exclude_none=False), stream, ensure_ascii=False, indent=2)
        (raw_directory / "docling.md").write_text(document.export_to_markdown(), encoding="utf-8")
        warnings = ["Docling reported partial_success"] if status == "partial_success" else []
        metadata = ParserMetadata(parser=self.name, parser_version=self.version, source_sha256=source_sha256,
            input_format=extension.lstrip("."), started_at=started, completed_at=datetime.now(timezone.utc),
            status=status, warnings=warnings, errors=errors, options=self.configuration)
        return ParserResult(self.iter_items(document, physical_pages=extension == ".pdf"), metadata,
            total_pages=len(document.pages) if extension == ".pdf" else None, raw_artifact_version=document.version)

    @staticmethod
    def iter_items(document, *, physical_pages: bool) -> Iterator[ParserItem]:
        """Convert public Docling items into neutral records, preserving reading order."""
        from docling_core.types.doc import PictureItem, SectionHeaderItem, TableItem, TextItem
        from docling_core.types.doc import ContentLayer
        labels = {"title": BlockType.TITLE, "section_header": BlockType.HEADING,
            "text": BlockType.PARAGRAPH, "paragraph": BlockType.PARAGRAPH,
            "list_item": BlockType.LIST_ITEM, "caption": BlockType.CAPTION,
            "footnote": BlockType.FOOTNOTE, "formula": BlockType.FORMULA,
            "code": BlockType.CODE, "picture": BlockType.FIGURE, "table": BlockType.TABLE,
            "quote": BlockType.QUOTE}
        for item, _ in document.iterate_items(included_content_layers=set(ContentLayer)):
            label = item.label.value
            kind = labels.get(label, BlockType.OTHER)
            caption = None
            if isinstance(item, TableItem):
                text = item.export_to_markdown(doc=document)
                caption = item.caption_text(document) or None
            elif isinstance(item, PictureItem):
                caption = item.caption_text(document) or None
                text = caption or ""
            elif isinstance(item, TextItem):
                text = item.text
            else:
                text = ""
            spans = []
            for provenance in item.prov:
                page = provenance.page_no if physical_pages else None
                box = provenance.bbox
                origin = box.coord_origin.value
                spans.append(ParserSpan(page_number=page,
                    bbox=BoundingBox(left=box.l, top=box.t, right=box.r, bottom=box.b,
                                     coordinate_origin={"TOPLEFT": "top-left", "BOTTOMLEFT": "bottom-left"}[origin]) if physical_pages else None,
                    locator=item.self_ref, raw_char_range=tuple(provenance.charspan)))
            yield ParserItem(type=kind, text=text, locator=item.self_ref,
                heading_level=item.level if isinstance(item, SectionHeaderItem) else None,
                spans=tuple(spans), caption=caption, raw_label=label)

    def load_raw(self, raw_json: Path, metadata: ParserMetadata) -> ParserResult:
        """Re-expose saved raw output through the same neutral boundary for future re-normalization."""
        from docling_core.types.doc import DoclingDocument
        document = DoclingDocument.model_validate_json(raw_json.read_text(encoding="utf-8"))
        physical = metadata.input_format == "pdf"
        return ParserResult(self.iter_items(document, physical_pages=physical), metadata,
            total_pages=len(document.pages) if physical else None, raw_artifact_version=document.version)
