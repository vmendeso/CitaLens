"""Single Compose initializer: migration first, scoped login passwords second."""
import os
from alembic import command
from alembic.config import Config
import psycopg
from psycopg import sql


def main():
    command.upgrade(Config("alembic.ini"), "head")
    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(url) as conn:
        for role, env in (("api_writer", "API_DB_PASSWORD"), ("knowledge_writer", "KNOWLEDGE_DB_PASSWORD"),
                          ("upload_writer", "UPLOAD_DB_PASSWORD")):
            conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(os.environ[env])))


if __name__ == "__main__":
    main()
