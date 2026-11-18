"""Every label a database enum type gets from a migration must be one the code stores.

Production, 2026-11-18: creating a DJ user answered 500. The model stores the role by
its NAME ("DJ"), and the migration had added the lower-case value ("dj") to the
user_role type, so the label the code writes was never in the type. SQLite (the test
suite) has no enum types, so nothing here could see it.
"""
import re

import app.models  # noqa: F401  (registers every table)
from app import main as app_main
from app.db.base import Base
from sqlalchemy import Enum


def _enum_types() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for table in Base.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, Enum) and col.type.name and col.type.native_enum:
                out.setdefault(col.type.name, set()).update(col.type.enums)
    return out


def _added_labels() -> list[tuple[str, str]]:
    import inspect

    src = inspect.getsource(app_main)
    return re.findall(r"ALTER TYPE (\w+) ADD VALUE (?:IF NOT EXISTS )?'([^']+)'", src)


def test_migrations_add_the_labels_the_code_stores():
    types = _enum_types()
    added = _added_labels()
    assert added, "no ALTER TYPE ... ADD VALUE found: has the migration code moved?"
    wrong = [f"{name}: '{label}' (the code stores {sorted(types[name])})"
             for name, label in added if name in types and label not in types[name]]
    assert not wrong, f"labels added to a database type that the code never writes: {wrong}"


def test_every_user_role_reaches_the_database_type():
    """The first roles came with the table; each later one needs its own ADD VALUE."""
    types = _enum_types()
    assert "user_role" in types
    first = {"ADMIN", "MANAGER", "VIEWER"}
    added = {label for name, label in _added_labels() if name == "user_role"}
    missing = types["user_role"] - first - added
    assert not missing, f"user_role labels with no migration: {sorted(missing)}"
