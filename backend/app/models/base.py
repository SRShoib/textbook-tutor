from sqlalchemy import Enum
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def str_enum(enum_cls, name: str) -> Enum:
    """SQLAlchemy's Enum binds a Python enum member's .name (e.g.
    "PROCESSING") by default, not its .value ("processing") — which is what
    the Postgres enum type created by the migration actually contains. Every
    status/role/type column uses this helper instead of calling Enum()
    directly, so that mismatch can't reappear on a new column."""
    return Enum(enum_cls, name=name, values_callable=lambda e: [member.value for member in e])
