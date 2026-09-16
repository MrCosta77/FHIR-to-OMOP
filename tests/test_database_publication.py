import pytest

from src.utils.database_publication import (
    DatabasePublicationBusyError,
    database_publication_lock,
)


def test_publication_lock_is_exclusive_and_released(tmp_path):
    database = tmp_path / "published.duckdb"

    with database_publication_lock(database):
        with pytest.raises(DatabasePublicationBusyError):
            with database_publication_lock(database):
                raise AssertionError("a second process boundary acquired the lock")

    with database_publication_lock(database):
        pass
