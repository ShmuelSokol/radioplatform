"""Enum columns against a real Postgres.

The rest of the suite runs on SQLite, where an enum column is plain text, so a role
or status the database's enum type does not know passes every test and fails on
production ("invalid input value for enum user_role: "DJ"", 2026-11-18: the label had
been added as 'dj'). These tests use the Postgres of CI (DATABASE_URL) or
PG_TEST_URL, in a schema of their own, and are skipped only where there is none.
"""
import os
import uuid

import pytest

PG_URL = os.environ.get("PG_TEST_URL") or os.environ.get("DATABASE_URL") or ""
IS_PG = PG_URL.startswith("postgresql")

pytestmark = pytest.mark.skipif(not IS_PG, reason="needs a Postgres (PG_TEST_URL or DATABASE_URL)")


def test_ci_runs_these_on_postgres():
    """CI has a Postgres service; if its address ever stops reaching the tests they
    would all skip silently. (This test is skipped with the rest when it does, so
    the check is on the variable CI sets.)"""
    assert IS_PG


if os.environ.get("CI") and not IS_PG:  # pragma: no cover
    raise RuntimeError("CI must run the enum tests on Postgres: DATABASE_URL is not a Postgres address")


async def _schema():
    import asyncpg
    from sqlalchemy.ext.asyncio import create_async_engine

    name = f"enum_t_{uuid.uuid4().hex[:10]}"
    raw = await asyncpg.connect(PG_URL.replace("postgresql+asyncpg://", "postgresql://"),
                                server_settings={"search_path": name})
    await raw.execute(f'CREATE SCHEMA "{name}"')
    engine = create_async_engine(PG_URL, connect_args={"server_settings": {"search_path": name}})
    return name, raw, engine


async def _drop(name, raw, engine):
    await engine.dispose()
    await raw.execute(f'DROP SCHEMA "{name}" CASCADE')
    await raw.close()


async def _create_all(engine):
    import app.models  # noqa: F401
    from app.db.base import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _labels(raw, type_name):
    rows = await raw.fetch("SELECT enumlabel FROM pg_enum WHERE enumtypid = to_regtype($1)::oid", type_name)
    return {r["enumlabel"] for r in rows}


@pytest.mark.asyncio
async def test_a_user_of_every_role_saves_on_an_old_database():
    """A database whose user_role type was created before DJ and SPONSOR existed
    (and got 'dj' in lower case, as production did): after the start-up step, a user
    of every role saves and reads back."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.main import add_missing_enum_labels
    from app.models.user import User, UserRole

    name, raw, engine = await _schema()
    try:
        await raw.execute("CREATE TYPE user_role AS ENUM ('ADMIN','MANAGER','VIEWER')")
        await raw.execute("ALTER TYPE user_role ADD VALUE 'dj'")
        await _create_all(engine)
        added = await add_missing_enum_labels(raw)
        assert set(added) == {"user_role.DJ", "user_role.SPONSOR"}
        assert await add_missing_enum_labels(raw) == []  # the second start adds nothing

        async with AsyncSession(engine, expire_on_commit=False) as db:
            for role in UserRole:
                db.add(User(email=f"{role.value}@example.test", hashed_password="x", role=role))
            await db.commit()
            saved = {u.role for u in (await db.execute(select(User))).scalars()}
        assert saved == set(UserRole)
    finally:
        await _drop(name, raw, engine)


@pytest.mark.asyncio
async def test_every_member_of_every_enum_column_is_a_label():
    """Fresh database: each enum type holds every member the code can send. And an
    old database missing the last member of each type gets it back at start-up."""
    from app.main import add_missing_enum_labels, model_enum_labels

    wanted = model_enum_labels()
    assert {"user_role", "alert_type", "campaign_status", "play_source"} <= set(wanted)

    name, raw, engine = await _schema()
    try:
        await _create_all(engine)
        for type_name, labels in wanted.items():
            assert await _labels(raw, type_name) >= set(labels), type_name
        assert await add_missing_enum_labels(raw) == []
    finally:
        await _drop(name, raw, engine)

    name, raw, engine = await _schema()
    try:
        for type_name, labels in wanted.items():
            listed = ",".join(f"'{label}'" for label in labels[:-1])
            await raw.execute(f"CREATE TYPE {type_name} AS ENUM ({listed})")
        added = await add_missing_enum_labels(raw)
        assert set(added) == {f"{t}.{labels[-1]}" for t, labels in wanted.items()}
        for type_name, labels in wanted.items():
            assert await _labels(raw, type_name) == set(labels), type_name
            for label in labels:  # the cast is what an INSERT does
                assert await raw.fetchval(f"SELECT $1::{type_name}::text", label) == label
    finally:
        await _drop(name, raw, engine)
