"""Create local secrets without modifying an existing configuration."""
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
target = root / ".env"
if target.exists():
    raise SystemExit(".env já existe; preservado.")
text = (root / ".env.example").read_text(encoding="utf-8")
for name in ("POSTGRES_PASSWORD", "API_DB_PASSWORD", "KNOWLEDGE_DB_PASSWORD", "UPLOAD_DB_PASSWORD",
             "RABBITMQ_PASSWORD", "INTERNAL_TOKEN", "JWT_SECRET", "WEAVIATE_API_KEY", "SEARXNG_SECRET_KEY"):
    text = text.replace(f"{name}=CHANGE_ME", f"{name}={secrets.token_hex(32)}")
target.write_text(text, encoding="utf-8")
print(".env criado com segredos locais aleatórios.")
