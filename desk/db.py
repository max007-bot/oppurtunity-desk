"""SQLite access, migrations and the demo/live guard.

Connections are short-lived: a Streamlit rerun must never inherit a half-open
write transaction. Foreign keys are enforced on every connection, and every
query is parameterised.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from .clock import Clock, SystemClock, to_iso
from .config import Config, Mode

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# Migration filenames are interpolated into the version stamp, so they are
# restricted to characters that cannot terminate a SQL string literal.
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9_-]+$")


class DatabaseError(RuntimeError):
    pass


class ModeViolation(DatabaseError):
    """Raised when an operation would cross the demo/live boundary."""


def new_id(prefix: str) -> str:
    """Stable local identifier. Readable prefixes keep debugging sane."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@contextmanager
def connect(path: Path | str, *, readonly: bool = False) -> Iterator[sqlite3.Connection]:
    """Open a short-lived connection with foreign keys on and rows as mappings."""
    target = Path(path)
    if not readonly:
        target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=15.0, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 15000")
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """All-or-nothing unit of work.

    Import batches rely on this: an invalid row must not leave half a batch
    applied, and a crash mid-import must leave the previous state intact.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


# -- migrations ----------------------------------------------------------


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def applied_versions(conn: sqlite3.Connection) -> set[str]:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if row is None:
        return set()
    return {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}


def backup_database(path: Path) -> Path | None:
    """Copy the database aside before a schema change. Returns the copy's path."""
    target = Path(path)
    if not target.exists():
        return None
    stamp = to_iso(SystemClock().now()).replace(":", "").replace("-", "")
    backup = target.with_name(f"{target.stem}.backup-{stamp}{target.suffix}")
    shutil.copy2(target, backup)
    return backup


def migrate(path: Path, *, mode: Mode, clock: Clock | None = None) -> list[str]:
    """Apply pending migrations and stamp the file with its mode.

    The mode stamp is what stops a demo replay writing into live data even if a
    path is misconfigured later.
    """
    clock = clock or SystemClock()
    pending_check_needed = Path(path).exists()
    if pending_check_needed:
        with connect(path) as conn:
            if _migration_files() and (
                {f.stem for f in _migration_files()} - applied_versions(conn)
            ):
                backup_database(Path(path))

    applied: list[str] = []
    with connect(path) as conn:
        done = applied_versions(conn)
        for sql_file in _migration_files():
            version = sql_file.stem
            if version in done:
                continue
            if not _SAFE_VERSION.match(version):
                raise DatabaseError(
                    f"migration filename {version!r} must contain only letters, digits, "
                    f"underscores and hyphens"
                )
            body = sql_file.read_text(encoding="utf-8")
            # executescript() commits any pending transaction before it runs, so the
            # BEGIN/COMMIT pair has to live inside the script itself for the whole
            # migration plus its version stamp to be one unit of work.
            stamp = to_iso(clock.now())
            script = "\n".join(
                [
                    "BEGIN;",
                    body,
                    "INSERT INTO schema_migrations (version, applied_at) "
                    f"VALUES ('{version}', '{stamp}');",
                    "COMMIT;",
                ]
            )
            try:
                conn.executescript(script)
            except Exception:
                conn.execute("ROLLBACK")
                raise
            applied.append(version)

        stored = get_meta(conn, "mode")
        if stored is None:
            set_meta(conn, "mode", mode.value)
        elif stored != mode.value:
            raise ModeViolation(
                f"database {path} was initialised in {stored!r} mode but opened as "
                f"{mode.value!r}; use the separate database file for that mode"
            )
    return applied


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else row["value"]


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


# -- database handle -----------------------------------------------------


class Database:
    """Thin handle bound to one config, so a caller cannot forget the mode."""

    def __init__(self, config: Config, clock: Clock | None = None) -> None:
        self.config = config
        self.clock = clock or SystemClock()

    @property
    def path(self) -> Path:
        return self.config.db_path

    @property
    def is_demo(self) -> bool:
        return self.config.is_demo

    def initialise(self) -> list[str]:
        return migrate(self.path, mode=self.config.mode, clock=self.clock)

    def exists(self) -> bool:
        return self.path.exists()

    @contextmanager
    def open(self) -> Iterator[sqlite3.Connection]:
        with connect(self.path) as conn:
            stored = get_meta(conn, "mode")
            if stored is not None and stored != self.config.mode.value:
                raise ModeViolation(
                    f"refusing to use {self.path}: stamped as {stored!r}, opened as "
                    f"{self.config.mode.value!r}"
                )
            yield conn

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        with self.open() as conn:
            with transaction(conn):
                yield conn

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self.open() as conn:
            return conn.execute(sql, tuple(params)).fetchall()

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self.open() as conn:
            return conn.execute(sql, tuple(params)).fetchone()

    def reset_demo(self) -> Path:
        """Delete the demo database only.

        Refuses in live mode, and refuses a file that is not stamped demo, so a
        misconfigured path can never wipe real data.
        """
        if self.config.mode is not Mode.DEMO:
            raise ModeViolation("reset is only available in demo mode")
        target = self.path
        if target.exists():
            with connect(target) as conn:
                stored = get_meta(conn, "mode")
            if stored != Mode.DEMO.value:
                raise ModeViolation(
                    f"refusing to delete {target}: it is stamped {stored!r}, not demo"
                )
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(str(target) + suffix)
                if candidate.exists():
                    candidate.unlink()
        self.initialise()
        return target


def record_change(
    conn: sqlite3.Connection,
    *,
    entity_type: str,
    entity_id: str,
    action: str,
    field_name: str | None = None,
    old_value: str | None = None,
    new_value: str | None = None,
    actor: str = "system",
    run_id: str | None = None,
    occurred_at: str,
    is_demo: bool,
) -> str:
    row_id = new_id("chg")
    conn.execute(
        """INSERT INTO change_log
           (id, run_id, entity_type, entity_id, field_name, old_value, new_value,
            action, actor, occurred_at, is_demo)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            row_id,
            run_id,
            entity_type,
            entity_id,
            field_name,
            old_value,
            new_value,
            action,
            actor,
            occurred_at,
            1 if is_demo else 0,
        ),
    )
    return row_id
