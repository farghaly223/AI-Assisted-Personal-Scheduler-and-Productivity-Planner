"""One SQLAlchemy Base and the shared profile ownership foreign key."""

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    metadata = sa.MetaData(schema="public")
    __mapper_args__ = {"eager_defaults": True}


def owner_fk(name: str) -> sa.ForeignKey:
    return sa.ForeignKey("public.profiles.id", name=name, ondelete="CASCADE", onupdate="RESTRICT")
