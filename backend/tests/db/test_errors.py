"""The Octave DB exception hierarchy — callers only ever catch these."""

from octave.db.errors import (
    DbAdapterLoadError,
    DbAdapterRegistrationError,
    DbConfigError,
    DbDimensionMismatchError,
    DbError,
    DbMigrationError,
    UnknownDbAdapterError,
)


def test_all_errors_subclass_base() -> None:
    for cls in (
        DbConfigError,
        DbMigrationError,
        DbDimensionMismatchError,
        DbAdapterRegistrationError,
        DbAdapterLoadError,
        UnknownDbAdapterError,
    ):
        assert issubclass(cls, DbError)


def test_dimension_mismatch_carries_dims() -> None:
    err = DbDimensionMismatchError(expected=768, actual=1024)
    assert err.expected == 768
    assert err.actual == 1024
    assert "768" in str(err) and "1024" in str(err)


def test_unknown_adapter_lists_known_names() -> None:
    err = UnknownDbAdapterError("pgvector", ["sqlite"])
    assert err.name == "pgvector"
    assert err.known == ["sqlite"]
    assert "sqlite" in str(err)
