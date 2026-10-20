"""Rebuild one user's document index from authoritative SQL chunks."""
import sys
from uuid import UUID
from packages.common.config import get_settings
from packages.common.http import call

if len(sys.argv) != 3:
    raise SystemExit("Uso: python scripts/reindex.py DOCUMENT_UUID USER_UUID")
document_id, user_id = (UUID(value) for value in sys.argv[1:])
result = call(get_settings().knowledge_url, f"/documents/{document_id}/reindex?user_id={user_id}",
              timeout=get_settings().stage_timeout)
print(result)
