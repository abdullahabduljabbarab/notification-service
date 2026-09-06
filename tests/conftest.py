import os

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.channels import Channel, ScriptedChannel
from app.database import get_db
from app.main import app, get_channels

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://notify:notify@localhost:5435/notify_test",
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ensure_database() -> None:
    """Create the test database if it does not exist, so the suite is
    self-bootstrapping against a running PostgreSQL server."""
    url = make_url(TEST_DATABASE_URL)
    server = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"),
            {"n": url.database},
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    server.dispose()


def _migrate() -> None:
    """Build the schema from the Alembic migrations, so tests run against the
    migrated schema rather than ORM metadata (ADR-014, carried from the risk
    engine). This is what keeps the ORM and the migrations honest with each
    other."""
    cfg = Config(os.path.join(_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(_ROOT, "migrations"))
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(cfg, "head")


_ensure_database()

engine = create_engine(TEST_DATABASE_URL)
TestSession = sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def setup_db():
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS notification_attempts CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS notification_deliveries CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS alembic_version CASCADE"))
    _migrate()
    yield


@pytest.fixture
def db():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def channels():
    """Scripted channels that always succeed, so API tests are deterministic.
    Individual tests can script failures via their own providers."""
    return {
        Channel.EMAIL: ScriptedChannel(Channel.EMAIL),
        Channel.SMS: ScriptedChannel(Channel.SMS),
    }


@pytest.fixture
def client(channels):
    def override_db():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_channels] = lambda: channels
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
