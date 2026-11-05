"""SQLAlchemy engine/session setup. Swapping DATABASE_URL to a Postgres DSN
is the only change needed to move off SQLite."""
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def initialize_database() -> None:
    Base.metadata.create_all(bind=engine)
    columns = {column["name"] for column in inspect(engine).get_columns("clone_jobs")}
    additions = {
        "sqa_property_url": "VARCHAR(1000)",
        "activation_status": "VARCHAR(50)",
        "activation_id": "VARCHAR(100)",
        "activation_network": "VARCHAR(20)",
        "change_summary": "JSON",
    }
    with engine.begin() as connection:
        for column, column_type in additions.items():
            if column not in columns:
                connection.execute(text(f"ALTER TABLE clone_jobs ADD COLUMN {column} {column_type}"))
        profile_columns = {column["name"] for column in inspect(connection).get_columns("clone_profiles")}
        if "attempt_activation" not in profile_columns:
            connection.execute(text("ALTER TABLE clone_profiles ADD COLUMN attempt_activation BOOLEAN NOT NULL DEFAULT FALSE"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
