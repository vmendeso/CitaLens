"""Initial reviewed PostgreSQL schema, constraints, indexes and domain privileges.

The SQL file is an immutable revision, deliberately independent of live ORM metadata.
"""
from pathlib import Path
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    for statement in sql.split(";\n"):
        if statement.strip():
            op.execute(statement)
    for role in ("api_writer", "knowledge_writer", "upload_writer"):
        op.execute(f"""DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            CREATE ROLE {role} LOGIN;
          END IF;
        END $$""")
    op.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA identity, orchestration, knowledge TO api_writer, knowledge_writer")
    op.execute("GRANT USAGE ON SCHEMA storage TO upload_writer")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA identity, orchestration, knowledge TO api_writer, knowledge_writer")
    op.execute("GRANT INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA identity, orchestration TO api_writer")
    op.execute("GRANT INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA knowledge TO knowledge_writer")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA storage TO upload_writer")


def downgrade():
    # Irreversible data loss: take an encrypted backup before executing this rollback.
    for schema in ("knowledge", "storage", "orchestration", "identity"):
        op.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    # Cluster roles may be used by other databases and are intentionally retained.
