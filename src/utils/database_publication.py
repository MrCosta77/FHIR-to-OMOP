"""Cross-process coordination for the atomically published DuckDB file."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path


class DatabasePublicationBusyError(RuntimeError):
    """Raised when another process owns the publication/write boundary."""


def _lock_path(database_path: str | Path) -> Path:
    database = Path(database_path)
    return database.with_suffix(f"{database.suffix}.publish.lock")


@contextmanager
def database_publication_lock(database_path: str | Path):
    """Hold a non-blocking OS lock shared by publication and portal writes."""
    lock_path = _lock_path(database_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise DatabasePublicationBusyError(
                    "The published database is being updated by another process; "
                    "retry after that operation completes."
                ) from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise DatabasePublicationBusyError(
                    "The published database is being updated by another process; "
                    "retry after that operation completes."
                ) from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
