"""Developer helper, never used at runtime. Review the emitted initial revision before committing."""
from pathlib import Path
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable, CreateIndex
from packages.common.models import Base
from packages.contracts.api import RunStatus

dialect = postgresql.dialect()
parts = [f"CREATE SCHEMA {name}" for name in ("identity", "orchestration", "storage", "knowledge")]
parts.append("CREATE TYPE orchestration.run_status AS ENUM (" +
             ", ".join(f"'{x.value}'" for x in RunStatus) + ")")
for table in Base.metadata.sorted_tables:
    parts.append(str(CreateTable(table).compile(dialect=dialect)).strip())
    for index in sorted(table.indexes, key=lambda i: i.name):
        parts.append(str(CreateIndex(index).compile(dialect=dialect)).strip())
Path("migrations/versions/0001_initial.sql").write_text(";\n\n".join(parts) + ";\n", encoding="utf-8")
print(f"Frozen {len(Base.metadata.tables)} tables with explicit constraints/indexes.")
