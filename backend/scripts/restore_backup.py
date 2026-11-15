"""Restore a KBR backup file into an EMPTY database.

    cd backend
    uv run python -m scripts.restore_backup kbr-backup-20261115-020000.json.gz \
        --database-url postgresql+asyncpg://user:pass@host:5432/newdb

Creates the tables if they do not exist, refuses if any table already has
rows, and prints the row count restored per table. It never deletes anything.
"""
import argparse
import asyncio
import sys

from sqlalchemy.ext.asyncio import create_async_engine

from app.db.base import Base
from app.services import backup_service


async def main(path: str, database_url: str) -> int:
    with open(path, "rb") as fh:
        backup = backup_service.unpack(fh.read())
    print(f"Backup taken {backup['created_at']}, {sum(backup['row_counts'].values())} rows")

    engine = create_async_engine(database_url, connect_args={"statement_cache_size": 0})
    try:
        async with engine.begin() as conn:
            import app.models  # noqa: F401
            await conn.run_sync(Base.metadata.create_all)
            restored = await backup_service.restore_database(conn, backup)
    except backup_service.DatabaseNotEmptyError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    finally:
        await engine.dispose()

    mismatches = 0
    for table, expected in backup["row_counts"].items():
        got = restored.get(table)
        flag = "" if got == expected else "   <-- MISMATCH"
        mismatches += got != expected
        if expected or flag:
            print(f"  {table:28} {got if got is not None else 'skipped':>8}{flag}")
    print("Restore complete." if not mismatches else f"Restore finished with {mismatches} mismatched tables.")
    return 1 if mismatches else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file")
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.file, args.database_url)))
