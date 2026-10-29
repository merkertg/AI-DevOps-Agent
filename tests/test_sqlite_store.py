"""Check the append-only SQLite store: idempotency, immutability and refusals."""

import os
import sqlite3
import stat
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.application.inputs import PreparedInput
from app.application.records import InvestigationRecord, build_record
from app.domain import Observation, ObservationKind
from app.infrastructure import sqlite_store
from app.infrastructure.fixtures import load_fixture
from app.infrastructure.sqlite_store import SqliteInvestigationStore, StoreError

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures/stage05/after_deployment.json"
OTHER = ROOT / "fixtures/stage03/partial_incident.json"
STORED_AT = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def record_for(
    prepared: PreparedInput, stored_at: datetime = STORED_AT
) -> InvestigationRecord:
    """Build a record for one prepared input at a given storage time."""
    return build_record(prepared, stored_at=stored_at, app_version="0.1.0")


def fixture_record(stored_at: datetime = STORED_AT) -> InvestigationRecord:
    """Build the record of the Stage 05 fixture."""
    return record_for(load_fixture(FIXTURE), stored_at)


def open_store(path: Path) -> SqliteInvestigationStore:
    """Open the store for appending."""
    return SqliteInvestigationStore.open(path, create=True)


def test_new_store_is_private_and_reads_back_exactly(tmp_path: Path) -> None:
    """A created file is owner-only; a stored record is returned byte for byte."""
    path = tmp_path / "var" / "investigations.sqlite3"
    item = fixture_record()
    with open_store(path) as store:
        assert store.add(item) == (item, True)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) & 0o077 == 0
    with SqliteInvestigationStore.open(path, create=False) as store:
        assert store.get(item.summary.investigation_id) == item
        assert store.count() == 1


def test_memory_named_relative_store_is_a_persistent_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SQLite's special name cannot silently turn a requested file into memory."""
    monkeypatch.chdir(tmp_path)
    path = Path(":memory:")
    item = fixture_record()
    with open_store(path) as store:
        assert store.add(item)[1] is True
    assert path.stat().st_size > 0
    with SqliteInvestigationStore.open(path, create=False) as store:
        assert store.get(item.summary.investigation_id) == item


def test_repeated_save_returns_the_original_record(tmp_path: Path) -> None:
    """The same input saved later keeps one row and its first storage time."""
    path = tmp_path / "store.sqlite3"
    first = fixture_record()
    with open_store(path) as store:
        store.add(first)
        again, created = store.add(fixture_record(STORED_AT + timedelta(days=1)))
        assert (again, created) == (first, False)
        assert store.count() == 1


def test_new_input_for_the_same_incident_adds_a_second_record(tmp_path: Path) -> None:
    """A new collection never replaces the earlier record of that incident."""
    path = tmp_path / "store.sqlite3"
    prepared = load_fixture(FIXTURE)
    first = record_for(prepared)
    extra = Observation(
        "obs-late-note",
        "api-log",
        prepared.incident.ends_at,
        prepared.incident.ends_at,
        ObservationKind.NOTE,
        message="Fictional later note.",
    )
    second = record_for(
        replace(prepared, observations=(*prepared.observations, extra)),
        STORED_AT + timedelta(minutes=5),
    )
    with open_store(path) as store:
        store.add(first)
        assert store.add(second)[1] is True
        assert store.get(first.summary.investigation_id) == first
        recent = store.recent(10)
    assert [item.investigation_id for item in recent] == [
        second.summary.investigation_id,
        first.summary.investigation_id,
    ]
    assert {item.incident_id for item in recent} == {"training-after-deployment"}


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE investigations SET report_document = 'x'",
        "DELETE FROM investigations",
        "INSERT OR REPLACE INTO investigations SELECT * FROM investigations",
    ],
)
def test_rows_cannot_be_changed_even_with_direct_sql(
    statement: str, tmp_path: Path
) -> None:
    """Triggers make the table append-only for any SQLite client."""
    path = tmp_path / "store.sqlite3"
    item = fixture_record()
    with open_store(path) as store:
        store.add(item)
    with sqlite3.connect(path) as connection, pytest.raises(sqlite3.Error):
        connection.execute(statement)
    with SqliteInvestigationStore.open(path, create=False) as store:
        assert store.get(item.summary.investigation_id) == item


def test_read_only_store_cannot_append(tmp_path: Path) -> None:
    """Reading commands open the file read-only and cannot add records."""
    path = tmp_path / "store.sqlite3"
    open_store(path).close()
    with SqliteInvestigationStore.open(path, create=False) as store:
        with pytest.raises(StoreError, match="read-only"):
            store.add(fixture_record())
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM investigations").fetchone() == (
            0,
        )


def test_missing_store_is_not_created_by_reading(tmp_path: Path) -> None:
    """A read of a missing store fails without creating a file."""
    path = tmp_path / "missing.sqlite3"
    with pytest.raises(StoreError, match="not found"):
        SqliteInvestigationStore.open(path, create=False)
    assert not path.exists()


def test_symbolic_link_store_is_refused(tmp_path: Path) -> None:
    """A store path must be a regular file, not a link to another file."""
    target = tmp_path / "target.sqlite3"
    open_store(target).close()
    link = tmp_path / "link.sqlite3"
    link.symlink_to(target)
    for create in (True, False):
        with pytest.raises(StoreError):
            SqliteInvestigationStore.open(link, create=create)


@pytest.mark.parametrize(
    "setup",
    [
        "CREATE TABLE other (value TEXT)",
        "PRAGMA user_version = 2",
        "CREATE TABLE investigations (value TEXT); PRAGMA user_version = 1",
    ],
    ids=["foreign-table", "future-version", "wrong-schema"],
)
def test_foreign_or_future_files_are_refused(setup: str, tmp_path: Path) -> None:
    """Only an empty file or exactly the known schema is accepted."""
    path = tmp_path / "store.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(setup)
    for create in (True, False):
        with pytest.raises(StoreError, match="supported investigation store"):
            SqliteInvestigationStore.open(path, create=create)


def test_dropped_trigger_makes_the_store_unsupported(tmp_path: Path) -> None:
    """A store whose immutability trigger was removed is not trusted."""
    path = tmp_path / "store.sqlite3"
    open_store(path).close()
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER investigations_no_update")
    with pytest.raises(StoreError, match="supported"):
        SqliteInvestigationStore.open(path, create=False)


@pytest.mark.parametrize(
    ("trigger", "operation"),
    [
        ("investigations_no_replace", "INSERT"),
        ("investigations_no_update", "UPDATE"),
        ("investigations_no_delete", "DELETE"),
    ],
)
def test_same_named_no_op_trigger_is_refused(
    trigger: str, operation: str, tmp_path: Path
) -> None:
    """Matching object names cannot disguise an absent append-only protection."""
    path = tmp_path / "store.sqlite3"
    open_store(path).close()
    with sqlite3.connect(path) as connection:
        connection.execute(f"DROP TRIGGER {trigger}")
        connection.execute(
            f"CREATE TRIGGER {trigger} BEFORE {operation} ON investigations "
            "BEGIN SELECT 1; END"
        )
    for create in (True, False):
        with pytest.raises(StoreError, match="supported"):
            SqliteInvestigationStore.open(path, create=create)


def test_table_definition_is_checked_alongside_trigger_bodies(tmp_path: Path) -> None:
    """A table with known names but weaker type constraints is unsupported."""
    path = tmp_path / "store.sqlite3"
    open_store(path).close()
    with sqlite3.connect(path) as connection:
        definitions = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type"
        ).fetchall()
        connection.execute("DROP TABLE investigations")
        for (statement,) in definitions:
            connection.execute(statement.removesuffix(" STRICT"))
    with pytest.raises(StoreError, match="supported"):
        SqliteInvestigationStore.open(path, create=False)


def test_non_sqlite_file_is_refused(tmp_path: Path) -> None:
    """A random file is reported as unreadable, not parsed or overwritten."""
    path = tmp_path / "store.sqlite3"
    path.write_bytes(b"fictional-private-marker " * 100)
    for create in (True, False):
        with pytest.raises(StoreError) as error:
            SqliteInvestigationStore.open(path, create=create)
        assert "fictional-private-marker" not in str(error.value)
    assert path.read_bytes().startswith(b"fictional-private-marker")


def test_unredacted_text_is_never_stored(tmp_path: Path) -> None:
    """Input that bypassed sanitizing is refused before any row is written."""
    prepared = load_fixture(FIXTURE)
    leaked = replace(
        prepared,
        observations=tuple(
            replace(event, message="Fictional token=fictional-leak-0000")
            if event.observation_id == "obs-refusal"
            else event
            for event in prepared.observations
        ),
    )
    path = tmp_path / "store.sqlite3"
    with open_store(path) as store:
        with pytest.raises(StoreError, match="redaction"):
            store.add(record_for(leaked))
        assert store.count() == 0
    assert b"fictional-leak-0000" not in path.read_bytes()


def test_inconsistent_record_is_refused(tmp_path: Path) -> None:
    """A record whose hashes or identity disagree with its documents is rejected."""
    item = fixture_record()
    forged = replace(item, report_document=item.report_document + " ")
    with open_store(tmp_path / "store.sqlite3") as store:
        with pytest.raises(StoreError, match="consistency"):
            store.add(forged)


def test_document_size_limits_apply_on_write_and_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Oversized documents are neither written nor loaded into memory."""
    path = tmp_path / "store.sqlite3"
    item = fixture_record()
    with open_store(path) as store:
        store.add(item)
    monkeypatch.setattr(sqlite_store, "MAX_DOCUMENT_BYTES", 100)
    with open_store(path) as store:
        with pytest.raises(StoreError, match="size limit"):
            store.add(record_for(load_fixture(OTHER)))
        with pytest.raises(StoreError, match="size limit"):
            store.get(item.summary.investigation_id)


def test_identifier_collision_is_refused_without_overwriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two inputs mapped to one identifier never replace each other."""
    path = tmp_path / "store.sqlite3"
    first = fixture_record()
    with open_store(path) as store:
        store.add(first)
        other = record_for(load_fixture(OTHER))
        colliding = replace(
            other,
            summary=replace(
                other.summary, investigation_id=first.summary.investigation_id
            ),
        )
        monkeypatch.setattr(
            sqlite_store, "verify_record", lambda _: {"status": "verified"}
        )
        with pytest.raises(StoreError, match="collision"):
            store.add(colliding)
        assert store.get(first.summary.investigation_id) == first


def test_unknown_or_malformed_identifiers_find_nothing(tmp_path: Path) -> None:
    """Lookups accept only the documented identifier format."""
    path = tmp_path / "store.sqlite3"
    with open_store(path) as store:
        store.add(fixture_record())
        assert store.get("inv-000000000000000000000000") is None
        assert store.get("inv-' OR '1'='1") is None


@pytest.mark.parametrize("limit", [0, 501, True])
def test_list_limit_is_bounded(limit: int, tmp_path: Path) -> None:
    """Listing refuses unbounded or invalid limits."""
    with open_store(tmp_path / "store.sqlite3") as store:
        with pytest.raises(StoreError, match="limit"):
            store.recent(limit)


def test_tampered_row_is_read_but_fails_verification(tmp_path: Path) -> None:
    """Edits made by bypassing the triggers are detected by the record hashes."""
    from app.application.records import integrity_problems

    path = tmp_path / "store.sqlite3"
    item = fixture_record()
    with open_store(path) as store:
        store.add(item)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER investigations_no_update")
        connection.execute(
            "UPDATE investigations SET report_document = report_document || ' '"
        )
        connection.executescript(
            "CREATE TRIGGER investigations_no_update BEFORE UPDATE ON investigations "
            "BEGIN SELECT RAISE(ABORT, 'investigations are append-only'); END"
        )
    with SqliteInvestigationStore.open(path, create=False) as store:
        tampered = store.get(item.summary.investigation_id)
    assert tampered is not None
    assert integrity_problems(tampered) == ["report_sha256"]


def test_concurrent_saves_of_one_input_create_one_row(tmp_path: Path) -> None:
    """Two connections saving the same input end with a single record."""
    path = tmp_path / "store.sqlite3"
    item = fixture_record()
    with open_store(path) as first, open_store(path) as second:
        results = [first.add(item)[1], second.add(item)[1]]
        assert results == [True, False]
        assert second.count() == 1


@pytest.mark.parametrize("column", ["input_document", "report_document"])
def test_repeating_save_refuses_a_corrupted_existing_record(
    column: str, tmp_path: Path
) -> None:
    """An idempotent save cannot silently return a row whose stored bytes changed."""
    path = tmp_path / "store.sqlite3"
    item = fixture_record()
    with open_store(path) as store:
        store.add(item)
    with sqlite3.connect(path) as connection:
        (trigger_sql,) = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name = 'investigations_no_update'"
        ).fetchone()
        connection.execute("DROP TRIGGER investigations_no_update")
        connection.execute(f"UPDATE investigations SET {column} = {column} || ' '")
        connection.execute(trigger_sql)
    with open_store(path) as store:
        with pytest.raises(StoreError, match="Existing investigation.*integrity"):
            store.add(item)
        assert store.count() == 1
        unchanged = store.get(item.summary.investigation_id)
    assert unchanged is not None
    assert getattr(unchanged, column) == getattr(item, column) + " "


def test_store_errors_do_not_disclose_paths(tmp_path: Path) -> None:
    """A store under an unwritable location fails with a static message."""
    locked = tmp_path / "fictional-private-dir"
    locked.mkdir(mode=0o500)
    try:
        with pytest.raises(StoreError) as error:
            SqliteInvestigationStore.open(locked / "store.sqlite3", create=True)
    finally:
        os.chmod(locked, 0o700)
    assert "fictional-private-dir" not in str(error.value)
