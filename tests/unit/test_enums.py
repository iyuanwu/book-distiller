import pytest
from book_distiller.core.enums import BookStatus, StageStatus, ErrorSeverity, DistillationMode


@pytest.mark.parametrize("enum,values", [
    (BookStatus, "pending processing paused completed failed needs_review"),
    (StageStatus, "pending running completed failed skipped needs_review"),
    (ErrorSeverity, "critical recoverable warning"),
    (DistillationMode, "fast standard deep"),
])
def test_enum_contract(enum, values):
    assert {item.value for item in enum} == set(values.split())
    for value in values.split():
        assert enum(value) == value
    with pytest.raises(ValueError):
        enum("unknown")
