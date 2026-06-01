from collections.abc import Generator
import os
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
CONFIGURED_DATABASE_PATH = Path(os.getenv("STORE_DB_PATH", "data/store.db"))
DATABASE_PATH: Path = (
    CONFIGURED_DATABASE_PATH
    if CONFIGURED_DATABASE_PATH.is_absolute()
    else PROJECT_ROOT / CONFIGURED_DATABASE_PATH
)
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

SQLALCHEMY_DATABASE_URL: str = f"sqlite:///{DATABASE_PATH.as_posix()}"

engine: Engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal: sessionmaker[Session] = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    class_=Session,
)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""


def get_db() -> Generator[Session, None, None]:
    """Yield a database session and close it after request handling."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all registered database tables."""
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
