"""Keep investigation records in one local, append-only SQLite file."""

import os
import sqlite3
import stat
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from app.application.inputs import ORIGINS, Origin, PreparedInput
from app.application.records import (
    INVESTIGATION_ID,
    InvestigationRecord,
    InvestigationSummary,
    integrity_problems,
    verify_record,
)
from app.application.snapshots import SnapshotError, decode_input
from app.infrastructure.redaction import redact_text

SCHEMA_VERSION = 1
MAX_DOCUMENT_BYTES = 8_388_608
MAX_RECENT = 500

_COLUMNS = (
    "investigation_id",
    "incident_id",
    "origin",
    "data_quality",
    "stored_at",
    "app_version",
    "input_sha256",
    "report_sha256",
    "input_version",
    "input_document",
    "report_version",
    "report_document",
)
_SUMMARY_COLUMNS = ", ".join(_COLUMNS[:8])
_ALL_COLUMNS = ", ".join(_COLUMNS)
_TRIGGERS = (
    "investigations_no_replace",
    "investigations_no_update",
    "investigations_no_delete",
)
# STRICT tables reject values of the wrong type. Triggers refuse every change,
# including INSERT OR REPLACE, which deletes a conflicting row without firing
# delete triggers.
_SCHEMA = (
    """CREATE TABLE investigations (
        investigation_id TEXT PRIMARY KEY NOT NULL,
        incident_id TEXT NOT NULL,
        origin TEXT NOT NULL,
        data_quality TEXT NOT NULL,
        stored_at TEXT NOT NULL,
        app_version TEXT NOT NULL,
        input_sha256 TEXT NOT NULL UNIQUE,
        report_sha256 TEXT NOT NULL,
        input_version INTEGER NOT NULL,
        input_document TEXT NOT NULL,
        report_version INTEGER NOT NULL,
        report_document TEXT NOT NULL
    ) STRICT""",
    """CREATE TRIGGER investigations_no_replace BEFORE INSERT ON investigations
    WHEN EXISTS (
        SELECT 1 FROM investigations
        WHERE investigation_id = NEW.investigation_id
        OR input_sha256 = NEW.input_sha256
    )
    BEGIN SELECT RAISE(ABORT, 'investigations are append-only'); END""",
    """CREATE TRIGGER investigations_no_update BEFORE UPDATE ON investigations
    BEGIN SELECT RAISE(ABORT, 'investigations are append-only'); END""",
    """CREATE TRIGGER investigations_no_delete BEFORE DELETE ON investigations
    BEGIN SELECT RAISE(ABORT, 'investigations are append-only'); END""",
    f"PRAGMA user_version = {SCHEMA_VERSION}",
)


class StoreError(ValueError):
    """Expose only safe static diagnostics for a store that cannot be used."""


def _texts(prepared: PreparedInput) -> Iterator[str]:
    """Yield every stored text so none can bypass the redaction check."""
    incident = prepared.incident
    yield incident.incident_id
    yield incident.service
    for source in prepared.sources:
        yield source.source_id
        yield source.service
    for event in prepared.observations:
        yield event.observation_id
        yield event.source_id
        yield event.message
    yield from prepared.gaps
    yield from prepared.truncated_observation_ids
    if prepared.history_repository is not None:
        yield prepared.history_repository


def _require_storable(record: InvestigationRecord) -> None:
    """Refuse records that are oversized, inconsistent or still contain secrets."""
    for document in (record.input_document, record.report_document):
        if len(document.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise StoreError("Investigation exceeds the store size limit.")
    if verify_record(record)["status"] != "verified":
        raise StoreError("Investigation record failed its consistency checks.")
    try:
        prepared = decode_input(record.input_document)
    except SnapshotError:
        raise StoreError("Investigation input is not a valid snapshot.") from None
    if any(redact_text(text)[0] != text for text in _texts(prepared)):
        raise StoreError(
            "Investigation input contains text the redaction filter would change; "
            "it was not stored."
        )


def _origin(value: object) -> Origin:
    """Accept only a supported origin read back from the file."""
    for origin in ORIGINS:
        if value == origin:
            return origin
    raise StoreError("Stored investigation is malformed.")


def _summary(row: tuple[object, ...]) -> InvestigationSummary:
    """Rebuild summary fields read from one row."""
    try:
        identifier, incident_id, origin, quality, stored_at, version, ih, rh = row
        if not all(
            isinstance(value, str)
            for value in (identifier, incident_id, quality, stored_at, version, ih, rh)
        ):
            raise StoreError("Stored investigation is malformed.")
        stored = datetime.fromisoformat(str(stored_at))
        if stored.utcoffset() is None:
            raise StoreError("Stored investigation is malformed.")
        return InvestigationSummary(
            investigation_id=str(identifier),
            incident_id=str(incident_id),
            origin=_origin(origin),
            data_quality=str(quality),
            stored_at=stored,
            app_version=str(version),
            input_sha256=str(ih),
            report_sha256=str(rh),
        )
    except ValueError as error:
        if isinstance(error, StoreError):
            raise
        raise StoreError("Stored investigation is malformed.") from None


class SqliteInvestigationStore:
    """Append records to one SQLite file and read them without changing rows."""

    def __init__(self, connection: sqlite3.Connection, *, writable: bool) -> None:
        """Wrap an open connection whose schema was already checked."""
        self._connection = connection
        self._writable = writable

    @classmethod
    def open(cls, path: Path, *, create: bool) -> Self:
        """Open for appending (creating a private file if needed) or read-only."""
        try:
            if create:
                connection = _open_writable(path)
            else:
                connection = _open_read_only(path)
        except sqlite3.Error:
            raise StoreError("Investigation store could not be opened.") from None
        try:
            _check_schema(connection, create=create)
        except BaseException:
            connection.close()
            raise
        return cls(connection, writable=create)

    def __enter__(self) -> Self:
        """Use the store as a context manager that closes its connection."""
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        """Close the connection when the block ends."""
        self.close()

    def close(self) -> None:
        """Release the SQLite connection."""
        self._connection.close()

    def add(self, record: InvestigationRecord) -> tuple[InvestigationRecord, bool]:
        """Insert a new record once; repeating the same input returns the original."""
        if not self._writable:
            raise StoreError("Investigation store was opened read-only.")
        _require_storable(record)
        summary = record.summary
        values = (
            summary.investigation_id,
            summary.incident_id,
            summary.origin,
            summary.data_quality,
            summary.stored_at.isoformat(),
            summary.app_version,
            summary.input_sha256,
            summary.report_sha256,
            record.input_version,
            record.input_document,
            record.report_version,
            record.report_document,
        )
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._select_record(
                    "input_sha256 = ?", (summary.input_sha256,)
                )
                if existing is not None and integrity_problems(existing):
                    raise StoreError(
                        "Existing investigation failed its integrity checks; "
                        "it was not changed."
                    )
                if existing is None:
                    self._connection.execute(
                        f"INSERT INTO investigations ({_ALL_COLUMNS}) "
                        f"VALUES ({', '.join('?' for _ in _COLUMNS)})",
                        values,
                    )
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
        except sqlite3.IntegrityError:
            raise StoreError(
                "Investigation identifier collision; the record was not stored."
            ) from None
        except sqlite3.Error:
            raise StoreError("Investigation store could not be written.") from None
        if existing is not None:
            return existing, False
        return record, True

    def get(self, investigation_id: str) -> InvestigationRecord | None:
        """Read one record by identifier within the document size limit."""
        if not INVESTIGATION_ID.fullmatch(investigation_id):
            return None
        try:
            return self._select_record("investigation_id = ?", (investigation_id,))
        except sqlite3.Error:
            raise StoreError("Investigation store could not be read.") from None

    def recent(self, limit: int) -> tuple[InvestigationSummary, ...]:
        """List up to limit summaries, newest storage time first."""
        if type(limit) is not int or not 1 <= limit <= MAX_RECENT:
            raise StoreError("List limit is outside the allowed range.")
        try:
            rows = self._connection.execute(
                f"SELECT {_SUMMARY_COLUMNS} FROM investigations "
                "ORDER BY stored_at DESC, investigation_id LIMIT ?",
                (limit,),
            ).fetchall()
        except sqlite3.Error:
            raise StoreError("Investigation store could not be read.") from None
        return tuple(_summary(tuple(row)) for row in rows)

    def count(self) -> int:
        """Return the number of stored records."""
        try:
            row = self._connection.execute(
                "SELECT count(*) FROM investigations"
            ).fetchone()
        except sqlite3.Error:
            raise StoreError("Investigation store could not be read.") from None
        return int(row[0])

    def _select_record(
        self, condition: str, parameters: tuple[str, ...]
    ) -> InvestigationRecord | None:
        """Fetch one row, checking stored document sizes before reading them."""
        sizes = self._connection.execute(
            "SELECT length(CAST(input_document AS BLOB)), "
            "length(CAST(report_document AS BLOB)) "
            f"FROM investigations WHERE {condition}",
            parameters,
        ).fetchone()
        if sizes is None:
            return None
        if any(int(size) > MAX_DOCUMENT_BYTES for size in sizes):
            raise StoreError("Stored investigation exceeds the size limit.")
        row = self._connection.execute(
            f"SELECT {_ALL_COLUMNS} FROM investigations WHERE {condition}",
            parameters,
        ).fetchone()
        values = tuple(row)
        input_version, input_document, report_version, report_document = values[8:]
        if (
            type(input_version) is not int
            or type(report_version) is not int
            or not isinstance(input_document, str)
            or not isinstance(report_document, str)
        ):
            raise StoreError("Stored investigation is malformed.")
        return InvestigationRecord(
            summary=_summary(values[:8]),
            input_version=input_version,
            input_document=input_document,
            report_version=report_version,
            report_document=report_document,
        )


def _regular_file(path: Path) -> bool:
    """Report whether path is an existing regular file and not a symbolic link."""
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _open_writable(path: Path) -> sqlite3.Connection:
    """Create a private store file once, then open it for appending."""
    try:
        if not path.parent.exists():
            path.parent.mkdir(mode=0o700, parents=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        os.close(os.open(path, flags, 0o600))
    except FileExistsError:
        pass
    except OSError:
        raise StoreError("Investigation store could not be created.") from None
    if not _regular_file(path):
        raise StoreError("Investigation store must be a regular file.")
    # An absolute path keeps a literal filename such as ':memory:' on disk.
    return sqlite3.connect(path.absolute(), timeout=5.0, isolation_level=None)


def _open_read_only(path: Path) -> sqlite3.Connection:
    """Open an existing store through SQLite's read-only URI mode."""
    if not _regular_file(path):
        raise StoreError("Investigation store was not found.")
    connection = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5.0
    )
    connection.execute("PRAGMA query_only = ON")
    return connection


def _normalized_sql(statement: str) -> str:
    """Compare schema definitions without treating harmless whitespace as a change."""
    return " ".join(statement.split()).rstrip(";")


def _schema_definitions(connection: sqlite3.Connection) -> dict[tuple[str, str], str]:
    """Read named user objects and their SQL, including the trigger bodies."""
    rows = connection.execute(
        "SELECT type, name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {
        (str(kind), str(name)): _normalized_sql(str(statement))
        for kind, name, statement in rows
    }


def _check_schema(connection: sqlite3.Connection, *, create: bool) -> None:
    """Create the schema in an empty file, or require exactly the known one."""
    expected = {("table", "investigations"): _normalized_sql(_SCHEMA[0])}
    expected.update(
        {
            ("trigger", name): _normalized_sql(statement)
            for name, statement in zip(_TRIGGERS, _SCHEMA[1:-1], strict=True)
        }
    )
    try:
        if create:
            connection.execute("BEGIN IMMEDIATE")
            try:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version == 0 and not _schema_definitions(connection):
                    for statement in _SCHEMA:
                        connection.execute(statement)
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        objects = _schema_definitions(connection)
    except sqlite3.Error:
        raise StoreError("File is not a readable investigation store.") from None
    if version != SCHEMA_VERSION or objects != expected:
        raise StoreError("File is not a supported investigation store.")
