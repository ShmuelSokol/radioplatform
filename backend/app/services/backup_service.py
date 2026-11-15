"""Whole-database backup and restore.

The backup is one gzip-compressed JSON file holding every row of every table
the application defines, plus a manifest of every audio file the rows point
at. It is written and read through the SQLAlchemy models, so it does not need
pg_dump and restores into any empty database the application can create.
"""
import base64
import datetime as dt
import decimal
import enum
import gzip
import json
import logging
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import settings
from app.db.base import Base

logger = logging.getLogger(__name__)

BACKUP_FORMAT = 1
_BATCH = 500

# (table, column) pairs that hold the location of an audio or image file.
FILE_COLUMNS = [
    ("assets", "file_path"),
    ("sponsors", "audio_file_path"),
    ("ad_drafts", "audio_file_path"),
    ("show_archives", "audio_url"),
    ("show_archives", "cover_image_url"),
    ("stations", "logo_url"),
    ("users", "photo_url"),
]


def _tables() -> list[sa.Table]:
    import app.models  # noqa: F401  (registers every model on Base.metadata)
    return list(Base.metadata.sorted_tables)


def _encode(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str, list, dict)):
        return value
    if isinstance(value, enum.Enum):
        return value.name
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (bytes, memoryview)):
        return base64.b64encode(bytes(value)).decode()
    return str(value)


def _decode(column: sa.Column, value: Any) -> Any:
    if value is None:
        return None
    t = column.type
    if isinstance(t, sa.Enum) and t.enum_class is not None:
        try:
            return t.enum_class[value]
        except KeyError:
            return t.enum_class(value)  # stored by value rather than by name
    if isinstance(t, sa.Uuid) or t.__class__.__name__ == "UUID":
        return uuid.UUID(value) if getattr(t, "as_uuid", True) else value
    if isinstance(t, sa.DateTime):
        return dt.datetime.fromisoformat(value)
    if isinstance(t, sa.Date):
        return dt.date.fromisoformat(value)
    if isinstance(t, sa.Time):
        return dt.time.fromisoformat(value)
    if isinstance(t, sa.Numeric) and not isinstance(t, sa.Float):
        return decimal.Decimal(value)
    if isinstance(t, sa.LargeBinary):
        return base64.b64decode(value)
    return value


def public_file_url(path: str | None) -> str | None:
    """Where a stored file can be downloaded from, or None if it has no file."""
    if not path:
        return None
    if path.startswith(("http://", "https://")):
        return path
    if settings.supabase_storage_enabled:
        return f"{settings.SUPABASE_URL}/storage/v1/object/public/{settings.SUPABASE_STORAGE_BUCKET}/{path}"
    return None


async def export_database(conn: AsyncConnection) -> dict:
    """Every row of every table, in an order that can be inserted back."""
    tables: dict[str, list[dict]] = {}
    for table in _tables():
        result = await conn.execute(sa.select(table))
        tables[table.name] = [
            {key: _encode(val) for key, val in row._mapping.items()} for row in result
        ]

    files: list[dict] = []
    seen: set[str] = set()
    for table_name, column in FILE_COLUMNS:
        for row in tables.get(table_name, []):
            path = row.get(column)
            if not path or path in seen:
                continue
            seen.add(path)
            files.append({"table": table_name, "id": row.get("id"), "path": path, "url": public_file_url(path)})

    return {
        "format": BACKUP_FORMAT,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "row_counts": {name: len(rows) for name, rows in tables.items()},
        "tables": tables,
        "files": files,
    }


def pack(backup: dict) -> bytes:
    return gzip.compress(json.dumps(backup, separators=(",", ":")).encode())


def unpack(data: bytes) -> dict:
    backup = json.loads(gzip.decompress(data))
    if backup.get("format") != BACKUP_FORMAT:
        raise ValueError(f"Unsupported backup format: {backup.get('format')!r}")
    return backup


class DatabaseNotEmptyError(RuntimeError):
    pass


async def restore_database(conn: AsyncConnection, backup: dict) -> dict[str, int]:
    """Load a backup into an EMPTY database. Refuses if any table has rows.

    Restoring never deletes anything: to roll a live station back, restore
    into a new empty database and point the application at it.
    """
    tables = _tables()
    for table in tables:
        count = (await conn.execute(sa.select(sa.func.count()).select_from(table))).scalar()
        if count:
            raise DatabaseNotEmptyError(
                f"Table {table.name} already has {count} rows; restore only into an empty database"
            )

    restored: dict[str, int] = {}
    for table in tables:
        rows = backup["tables"].get(table.name, [])
        columns = {c.name: c for c in table.columns}
        decoded = [
            {key: _decode(columns[key], val) for key, val in row.items() if key in columns}
            for row in rows
        ]
        for start in range(0, len(decoded), _BATCH):
            await conn.execute(sa.insert(table), decoded[start:start + _BATCH])
        restored[table.name] = len(decoded)

    unknown = sorted(set(backup["tables"]) - {t.name for t in tables})
    if unknown:
        logger.warning("Backup holds tables this version does not define, skipped: %s", unknown)
    return restored
