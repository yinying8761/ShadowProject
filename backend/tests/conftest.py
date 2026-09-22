"""
Shared pytest fixtures for backend tests.
"""

import pytest

# Pre-group conversations shape (what create_all built before ADR-0004):
# character_id NOT NULL, no group_id / last_extract_at. Migration tests use it
# to simulate an old database (SQLite cannot DROP a NOT NULL / FK column, so
# the old table is rebuilt by hand rather than altered).
OLD_CONVERSATIONS_DDL = (
    "CREATE TABLE conversations ("
    " id VARCHAR(36) NOT NULL PRIMARY KEY,"
    " character_id VARCHAR(36) NOT NULL"
    "   REFERENCES character_profiles(id) ON DELETE CASCADE,"
    " title VARCHAR(200) NOT NULL,"
    " summary TEXT,"
    " created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,"
    " updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL)"
)


@pytest.fixture
def old_conversations_ddl() -> str:
    return OLD_CONVERSATIONS_DDL
