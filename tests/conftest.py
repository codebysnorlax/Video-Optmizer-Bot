import pytest
from database.db import init_db

@pytest.fixture(autouse=True)
async def initialize_test_database():
    """Ensure SQLite database schema is created for every test run."""
    await init_db()
