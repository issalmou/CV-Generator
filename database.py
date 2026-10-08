"""
CV Assistant — database layer (SQLAlchemy 2.0, synchronous).

A single ``Engine`` / ``SessionLocal`` built from ``settings.DATABASE_URL``.
The production target is PostgreSQL; the test suite points the same code at
a throwaway SQLite database via the environment.

Schema management is deliberately minimal — ``init_db()`` runs
``Base.metadata.create_all()`` on startup (no Alembic). The models are
plain, DB-agnostic column types so the same definitions work on both
backends.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model."""


def _make_engine():
    url = settings.DATABASE_URL or "sqlite:///./cv_generator.db"
    connect_args: dict = {}
    kwargs: dict = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        # TestClient / threaded workers share the connection.
        connect_args = {"check_same_thread": False}
    else:
        # PostgreSQL (production): recycle connections before the server /
        # a firewall drops an idle one, and cap how long a single statement
        # may run so a pathological query can't pin a worker forever.
        kwargs["pool_recycle"] = 1800
        ms = int(getattr(settings, "DB_STATEMENT_TIMEOUT_MS", 0) or 0)
        if ms > 0:
            connect_args["options"] = f"-c statement_timeout={ms}"
    return create_engine(url, connect_args=connect_args, **kwargs)


engine = _make_engine()

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


def get_db() -> Iterator[Session]:
    """FastAPI dependency — yields a session, rolls back on a request error,
    always closes it."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """Create every table declared on ``Base`` and add any missing *additive*
    columns to tables that already exist. Idempotent.

    Schema management stays deliberately Alembic-free. ``create_all`` handles
    new tables; ``_ensure_additive_columns`` handles new **nullable / defaulted**
    columns on tables a previous release already created (``create_all`` never
    ALTERs). Anything more than an additive nullable column (a drop, a rename, a
    NOT NULL backfill, a type change) still needs a hand-written migration.
    """
    # Import models for their side effect: registering the tables on Base.
    import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_additive_columns()
    _ensure_unique_email()
    _ensure_document_references()
    _ensure_document_unique_constraints()
    logger.info("[Database] Schema ready (%d tables).", len(Base.metadata.tables))


def _ensure_document_references() -> None:
    """LOT 8 — back-fill ``reference`` / ``version`` on generated_cvs /
    generated_letters rows created before versioning existed. Idempotent: only
    rows with a NULL reference are touched, each getting a fresh reference and
    ``version = 1``. Never rewrites an existing reference."""
    from sqlalchemy import inspect as _inspect

    inspector = _inspect(engine)
    tables = set(inspector.get_table_names())
    plan = [("generated_cvs", "CV"), ("generated_letters", "LETTER")]
    with engine.begin() as conn:
        for table, prefix in plan:
            if table not in tables:
                continue
            cols = {c["name"] for c in inspector.get_columns(table)}
            if "reference" not in cols or "version" not in cols:
                continue
            rows = conn.exec_driver_sql(
                f'SELECT id FROM "{table}" WHERE reference IS NULL'
            ).fetchall()
            for (row_id,) in rows:
                ref = _mint_reference(prefix)
                conn.exec_driver_sql(
                    f'UPDATE "{table}" SET reference = %(ref)s, version = 1 WHERE id = %(id)s'
                    if engine.dialect.name != "sqlite" else
                    f'UPDATE "{table}" SET reference = ?, version = 1 WHERE id = ?',
                    (ref, row_id) if engine.dialect.name == "sqlite" else {"ref": ref, "id": row_id},
                )
            if rows:
                logger.info("[Database] back-filled %d %s reference(s)", len(rows), table)


def _mint_reference(prefix: str) -> str:
    """``CV_A8F42K`` / ``LETTER_91BC72`` — prefix + 6 Crockford-ish base32 chars."""
    import secrets
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no I/O/0/1
    return f"{prefix}_" + "".join(secrets.choice(alphabet) for _ in range(6))


def _ensure_document_unique_constraints() -> None:
    """LOT 8 hardening — a real DB UNIQUE INDEX on ``(reference, version)`` for
    generated_cvs / generated_letters so two concurrent requests (or two
    workers) can never mint the same version of one document. ``next_version``
    already guards it in application code; this closes the race at the DB.

    Idempotent (``CREATE UNIQUE INDEX IF NOT EXISTS`` on SQLite + PostgreSQL),
    data-preserving. If pre-existing rows already violate uniqueness the index
    creation would fail — we detect that first and log instead of crashing at
    boot; the operator then dedups manually."""
    from sqlalchemy import inspect as _inspect

    inspector = _inspect(engine)
    tables = set(inspector.get_table_names())
    plan = [
        ("generated_cvs", "uq_generated_cvs_reference_version"),
        ("generated_letters", "uq_generated_letters_reference_version"),
    ]
    with engine.begin() as conn:
        for table, index_name in plan:
            if table not in tables:
                continue
            cols = {c["name"] for c in inspector.get_columns(table)}
            if not {"reference", "version"} <= cols:
                continue
            if any(ix["name"] == index_name for ix in inspector.get_indexes(table)):
                continue
            dupes = conn.exec_driver_sql(
                f'SELECT reference, version FROM "{table}" '
                f'WHERE reference IS NOT NULL GROUP BY reference, version HAVING COUNT(*) > 1'
            ).fetchall()
            if dupes:
                logger.error(
                    "[Database] %s has %d duplicate (reference, version) pair(s); "
                    "cannot add the UNIQUE index. Dedup manually, then restart.",
                    table, len(dupes),
                )
                continue
            conn.exec_driver_sql(
                f'CREATE UNIQUE INDEX IF NOT EXISTS "{index_name}" '
                f'ON "{table}" (reference, version)'
            )
            logger.info("[Database] Added UNIQUE index %s", index_name)


def _ensure_additive_columns() -> None:
    """``ALTER TABLE ADD COLUMN`` for every model column missing from a live
    table. Safe on SQLite + PostgreSQL because every such column is declared
    nullable or with a default. Never drops or alters an existing column."""
    from sqlalchemy import inspect as _inspect
    from sqlalchemy.schema import CreateColumn

    inspector = _inspect(engine)
    existing_tables = set(inspector.get_table_names())
    dialect = engine.dialect

    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # brand-new table — create_all already made it in full
            live_cols = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in live_cols:
                    continue
                add = column
                if not column.nullable and column.server_default is None:
                    # SQLite (and PG) reject `ADD COLUMN ... NOT NULL` without a
                    # SQL-level default. A client-side ORM default only fills new
                    # rows via the ORM, not an ALTER — so add the column NULLABLE
                    # (data-safe; the ORM still defaults new rows).
                    add = column._copy()
                    add.nullable = True
                    logger.info(
                        "[Database] %s.%s added as NULLABLE (model is NOT NULL but has "
                        "no server_default) — tighten with a manual migration if needed.",
                        table.name, column.name,
                    )
                ddl = str(CreateColumn(add).compile(dialect=dialect))
                conn.exec_driver_sql(f'ALTER TABLE "{table.name}" ADD COLUMN {ddl}')
                logger.info("[Database] Added column %s.%s", table.name, column.name)

    # Indexes declared on the models but missing from live tables (create_all
    # only indexes tables it creates). Additive and safe to (re)issue.
    inspector = _inspect(engine)   # refresh after the ADD COLUMNs
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        live_indexes = {ix["name"] for ix in inspector.get_indexes(table.name)}
        live_cols = {c["name"] for c in inspector.get_columns(table.name)}
        for index in table.indexes:
            if index.name in live_indexes:
                continue
            if not {c.name for c in index.columns} <= live_cols:
                continue  # a column the index needs still isn't there
            try:
                index.create(bind=engine)
                logger.info("[Database] Created index %s", index.name)
            except Exception as exc:  # noqa: BLE001 — never fatal at boot
                logger.warning("[Database] Could not create index %s: %s", index.name, exc)


def _ensure_unique_email() -> None:
    """Defensive, idempotent check that ``users.email`` is actually enforced
    UNIQUE at the database level — belt-and-braces on top of the model's own
    ``unique=True`` (which ``create_all`` already applies for a brand-new
    table; this repairs a deployment where the table predates that, or where
    an operator's manual DDL dropped it). Never fatal: a table that already
    has duplicate rows (a pre-existing data problem, not something this
    migration should paper over) just logs and leaves the constraint missing."""
    from sqlalchemy import inspect as _inspect

    inspector = _inspect(engine)
    if "users" not in inspector.get_table_names():
        return
    has_unique = any(
        ix.get("unique") and list(ix.get("column_names") or []) == ["email"]
        for ix in inspector.get_indexes("users")
    ) or any(
        list(uc.get("column_names") or []) == ["email"]
        for uc in inspector.get_unique_constraints("users")
    )
    if has_unique:
        return
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                'CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email_unique ON "users" (email)'
            )
        logger.info("[Database] Added missing UNIQUE constraint on users.email")
    except Exception as exc:  # noqa: BLE001 — e.g. duplicate rows already exist
        logger.warning(
            "[Database] Could not enforce UNIQUE on users.email (%s) — "
            "check for pre-existing duplicate rows.", exc,
        )
