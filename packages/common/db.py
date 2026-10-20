from contextlib import contextmanager
from functools import lru_cache
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from packages.common.config import get_settings


@lru_cache
def engine():
    return create_engine(get_settings().database_url, pool_pre_ping=True, pool_recycle=300)


def get_db():
    with sessionmaker(engine(), expire_on_commit=False)() as session:
        yield session


@contextmanager
def transaction():
    with sessionmaker(engine(), expire_on_commit=False)() as session:
        with session.begin():
            yield session


def readiness():
    with engine().connect() as conn:
        conn.execute(text("SELECT 1"))
