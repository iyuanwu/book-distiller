"""Public Docling models/converter mocks; no conversion pipeline or downloads."""
from types import SimpleNamespace
import json
from pathlib import Path
import pytest
from docling_core.types.doc import DoclingDocument, DocItemLabel, ProvenanceItem, BoundingBox, CoordOrigin, TableData, Size
from book_distiller.parsers.docling import DoclingAdapter
from book_distiller.core.errors import PipelineError


def document_fixture():
    doc = DoclingDocument(name="Original adapter fixture")
    doc.add_page(page_no=1, size=Size(width=600, height=800))
    prov = ProvenanceItem(page_no=1, charspan=(0, 7), bbox=BoundingBox(l=10,t=30,r=100,b=10,coord_origin=CoordOrigin.BOTTOMLEFT))
    doc.add_heading("Chapter", level=2, prov=prov)
    doc.add_text(label=DocItemLabel.TEXT, text="Original paragraph", prov=prov)
    doc.add_table(data=TableData(num_rows=0, num_cols=0, table_cells=[]), prov=prov)
    doc.add_picture(prov=prov)
    doc.add_text(label=DocItemLabel.FORMULA, text="x = 1", prov=prov)
    doc.add_text(label=DocItemLabel.CODE, text="print(1)", prov=prov)
    return doc


def test_adapter_neutral_mapping():
    items = list(DoclingAdapter.iter_items(document_fixture(), physical_pages=True))
    assert [item.type.value for item in items] == ["heading","paragraph","table","figure","formula","code"]
    assert items[0].heading_level == 2
    assert items[0].spans[0].page_number == 1
    assert items[0].spans[0].bbox.coordinate_origin == "bottom-left"
    assert items[1].locator.startswith("#/")
    no_pages = list(DoclingAdapter.iter_items(document_fixture(), physical_pages=False))
    assert all(span.page_number is None and span.bbox is None for item in no_pages for span in item.spans)


def test_adapter_exports_raw_with_public_converter(monkeypatch, sample_source, tmp_path):
    import docling.document_converter as module
    doc = document_fixture()
    class Converter:
        def __init__(self, **kwargs):
            pass
        def convert(self, path, **kwargs):
            assert isinstance(path, Path)
            assert path.read_bytes() == sample_source.read_bytes()
            return SimpleNamespace(document=doc, status=SimpleNamespace(value="partial_success"),
                                   errors=[SimpleNamespace(error_message="Original warning")])
    monkeypatch.setattr(module, "DocumentConverter", Converter)
    raw = tmp_path / "raw"; raw.mkdir()
    result = DoclingAdapter().parse(sample_source, raw, "a"*64)
    assert result.metadata.status == "partial_success"
    assert result.metadata.errors == ["Original warning"]
    assert json.loads((raw / "docling.json").read_text())["schema_name"] == "DoclingDocument"
    assert (raw / "docling.md").is_file()
    assert result.total_pages is None
    loaded = DoclingAdapter().load_raw(raw / "docling.json", result.metadata)
    assert list(loaded.items) == list(result.items)


def test_docling_failure_not_success(monkeypatch, sample_source, tmp_path):
    import docling.document_converter as module
    class Converter:
        def __init__(self, **kwargs): pass
        def convert(self, *args, **kwargs):
            return SimpleNamespace(document=document_fixture(), status=SimpleNamespace(value="failure"), errors=[])
    monkeypatch.setattr(module, "DocumentConverter", Converter)
    with pytest.raises(PipelineError, match="conversion failure"):
        DoclingAdapter().parse(sample_source, tmp_path, "a"*64)


def test_canonical_code_has_no_docling_imports():
    import ast
    root = Path(__file__).parents[2] / "book_distiller"
    for folder in (root / "core/models", root / "normalize"):
        for path in folder.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom):
                    assert not (node.module or "").startswith("docling")
                elif isinstance(node, ast.Import):
                    assert all(not alias.name.startswith("docling") for alias in node.names)
