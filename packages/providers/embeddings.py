"""Explicit configurable embedding adapters; no fake embeddings in real mode."""
import math

from packages.providers.http import JsonProvider, ProviderError


class EmbeddingProvider:
    def __init__(self, settings):
        self.settings = settings
        self.http = JsonProvider(timeout=settings.embedding_timeout)

    @property
    def metadata(self):
        return {"provider": self.settings.embedding_provider, "model": self.settings.embedding_model,
                "dimension": self.settings.embedding_dimensions, "version": self.settings.embedding_version}

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        settings = self.settings
        if settings.embedding_provider == "none":
            raise ProviderError("Embeddings não configurados; recuperação lexical permanece disponível.")
        if any(not text.strip() or len(text) > 24000 for text in texts):
            raise ProviderError("Texto vazio ou acima do limite de embeddings.")
        vectors = []
        for start in range(0, len(texts), settings.embedding_batch_size):
            group = texts[start:start + settings.embedding_batch_size]
            if settings.embedding_provider == "openai":
                if not settings.openai_api_key:
                    raise ProviderError("OPENAI_API_KEY ausente para embeddings.")
                data = self.http.request("POST", settings.embedding_base_url.rstrip("/") + "/embeddings",
                    payload={"model": settings.embedding_model, "input": group,
                             "encoding_format": "float", "dimensions": settings.embedding_dimensions},
                    headers={"Authorization": f"Bearer {settings.openai_api_key}"}, max_attempts=1)
                batch = [item["embedding"] for item in sorted(data.get("data", []), key=lambda x: x["index"])]
            elif settings.embedding_provider == "ollama":
                data = self.http.request("POST", settings.embedding_base_url.rstrip("/") + "/api/embed",
                    payload={"model": settings.embedding_model, "input": group, "truncate": False},
                    max_attempts=1)
                batch = data.get("embeddings", [])
            else:
                raise ProviderError("Provedor de embeddings não suportado.")
            if len(batch) != len(group) or any(len(v) != settings.embedding_dimensions for v in batch):
                raise ProviderError("Dimensão ou quantidade de embeddings divergente; reindexação necessária.")
            if any(not isinstance(n, (float, int)) or not math.isfinite(n) for v in batch for n in v):
                raise ProviderError("Embedding inválido retornado pelo provedor.")
            vectors.extend(batch)
        return vectors
