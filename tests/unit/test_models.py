import pytest
from pydantic import ValidationError
from book_distiller.core.models import BookIdentity, EditionIdentity, RunMetadata, StageRecord, ErrorRecord


def test_records_roundtrip():
    book = BookIdentity(title="Sample")
    edition = EditionIdentity(book_id=book.book_id)
    run = RunMetadata(book_id=book.book_id, edition_id=edition.edition_id)
    stage = StageRecord(run_id=run.run_id, name="example")
    error = ErrorRecord(severity="warning", message="Example", run_id=run.run_id)
    for record in (book, edition, run, stage, error):
        assert type(record).model_validate_json(record.model_dump_json()) == record
    assert run.created_at.tzinfo is not None
    assert stage.status == "pending"


@pytest.mark.parametrize("model,data", [
    (BookIdentity, {"title": "  "}),
    (BookIdentity, {"title": "A", "unknown": 1}),
    (EditionIdentity, {"book_id": "bad-id"}),
    (StageRecord, {"run_id": "bad-id", "name": "test"}),
    (ErrorRecord, {"severity": "fatal", "message": "test"}),
])
def test_invalid_records(model, data):
    with pytest.raises(ValidationError):
        model(**data)


def test_independent_defaults():
    first, second = BookIdentity(title="A"), BookIdentity(title="B")
    first.authors.append("Author")
    assert second.authors == []
    assert first.book_id != second.book_id
