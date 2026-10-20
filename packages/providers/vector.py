"""Weaviate v4, isolated origins, deterministic identities and inspected failures."""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from uuid import NAMESPACE_URL, UUID, uuid5

from packages.providers.embeddings import EmbeddingProvider
from packages.providers.http import ProviderError

STRING_PROPERTIES = [
    "chunk_id", "user_id", "document_id", "document_version_id", "work_id", "source_id",
    "version", "origin", "content_hash", "embedding_model", "embedding_version",
]
INT_PROPERTIES = ["page_start", "page_end", "offset_start", "offset_end", "ordinal", "embedding_dimension"]


def stable_vector_id(chunk: dict) -> str:
    payload = [str(chunk.get(key, "")) for key in (
        "user_id", "document_id", "document_version_id", "source_id", "work_id", "version",
        "origin", "page_start", "page_end", "offset_start", "offset_end", "content_hash"
    )]
    return str(uuid5(NAMESPACE_URL, json.dumps(payload, ensure_ascii=False)))


def validate_chunk(chunk: dict, user_id: str, document_id: str):
    if chunk.get("user_id") != user_id or chunk.get("document_id") != document_id:
        raise ValueError("Chunk pertence a outro usuário ou documento.")
    if chunk.get("origin") not in {"SOURCE", "MANUSCRIPT"}:
        raise ValueError("Origem do chunk inválida.")
    if chunk["origin"] == "SOURCE" and (not chunk.get("source_id") or not chunk.get("work_id")):
        raise ValueError("Fonte exige identificadores de obra e documento-fonte.")
    if not chunk.get("version") or not chunk.get("id") or not chunk.get("text", "").strip():
        raise ValueError("Chunk sem versão, ID ou texto.")
    if hashlib.sha256(chunk["text"].encode()).hexdigest() != chunk.get("content_hash"):
        raise ValueError("Hash do chunk divergente.")
    if int(chunk.get("page_start", 0)) < 1 or int(chunk.get("page_end", 0)) < int(chunk["page_start"]):
        raise ValueError("Intervalo de páginas inválido.")
    if int(chunk.get("offset_start", -1)) < 0 or int(chunk.get("offset_end", -1)) < int(chunk["offset_start"]):
        raise ValueError("Offsets inválidos.")


class VectorStore:
    def __init__(self, settings):
        self.settings = settings
        self.embedding = EmbeddingProvider(settings)
        # A model, dimension or version change creates an independent index.
        fingerprint = hashlib.sha256(json.dumps(self.embedding.metadata, sort_keys=True).encode()).hexdigest()[:12]
        self.collections = {"SOURCE": f"SourceChunks_{fingerprint}", "MANUSCRIPT": f"ManuscriptChunks_{fingerprint}"}

    @contextmanager
    def connect(self):
        import weaviate
        from weaviate.classes.init import AdditionalConfig, Auth, Timeout
        config = self.settings
        client = weaviate.connect_to_custom(
            http_host=config.weaviate_host, http_port=config.weaviate_http_port, http_secure=False,
            grpc_host=config.weaviate_host, grpc_port=config.weaviate_grpc_port, grpc_secure=False,
            auth_credentials=Auth.api_key(config.weaviate_api_key) if config.weaviate_api_key else None,
            additional_config=AdditionalConfig(timeout=Timeout(init=10, query=30, insert=60)),
        )
        try:
            yield client
        finally:
            client.close()

    def collection(self, client, origin):
        from weaviate.classes.config import Configure, DataType, Property, Tokenization
        name = self.collections[origin]
        if not client.collections.exists(name):
            try:
                client.collections.create(
                    name=name, vector_config=Configure.Vectors.self_provided(),
                    properties=[Property(name="text", data_type=DataType.TEXT)] +
                    [Property(name=p, data_type=DataType.TEXT, tokenization=Tokenization.FIELD) for p in STRING_PROPERTIES] +
                    [Property(name=p, data_type=DataType.INT) for p in INT_PROPERTIES],
                )
            except Exception:
                # Concurrent creators can race; other creation errors must propagate.
                if not client.collections.exists(name):
                    raise
        return client.collections.use(name)

    def index(self, request):
        chunks = request.chunks
        for chunk in chunks:
            validate_chunk(chunk, request.user_id, request.document_id)
        result = {"indexed_ids": [], "failed_ids": [], "embedding": self.embedding.metadata, "limitations": []}
        if not chunks:
            return result
        try:
            vectors = self.embedding.embed([chunk["text"] for chunk in chunks])
        except ProviderError as exc:
            result["failed_ids"] = [chunk["id"] for chunk in chunks]
            result["limitations"].append(str(exc))
            return result
        try:
            with self.connect() as client:
                for origin in ("SOURCE", "MANUSCRIPT"):
                    group = [(c, v) for c, v in zip(chunks, vectors) if c["origin"] == origin]
                    if not group:
                        continue
                    collection = self.collection(client, origin)
                    ids = {}
                    with collection.batch.fixed_size(batch_size=64, concurrent_requests=1) as batch:
                        for chunk, vector in group:
                            vector_id = stable_vector_id(chunk)
                            ids[vector_id] = chunk["id"]
                            if collection.data.exists(UUID(vector_id)):
                                # Same deterministic hash/location is already reconciled.
                                result["indexed_ids"].append(chunk["id"])
                                continue
                            props = {p: str(chunk.get(p) or "") for p in STRING_PROPERTIES}
                            props.update({p: int(chunk.get(p) or 0) for p in INT_PROPERTIES})
                            props.update(text=chunk["text"], chunk_id=chunk["id"],
                                         embedding_model=self.settings.embedding_model,
                                         embedding_version=self.settings.embedding_version,
                                         embedding_dimension=self.settings.embedding_dimensions)
                            batch.add_object(properties=props, uuid=UUID(vector_id), vector=vector)
                    failures = collection.batch.failed_objects
                    failed_vector_ids = set()
                    for failure in failures:
                        identifier = str(getattr(getattr(failure, "object_", None), "uuid", ""))
                        if identifier in ids:
                            failed_vector_ids.add(identifier)
                        else:
                            # Unknown failure identity: fail closed and let reconciliation re-read.
                            failed_vector_ids.update(ids)
                    result["failed_ids"].extend(ids[i] for i in failed_vector_ids)
                    result["indexed_ids"].extend(c["id"] for c, _ in group if stable_vector_id(c) not in failed_vector_ids)
                    if failures:
                        result["limitations"].append(f"Weaviate rejeitou {len(failures)} objetos do lote; reindexar IDs falhos.")
        except Exception as exc:
            result["limitations"].append(f"Indexação indisponível: {type(exc).__name__}. PostgreSQL permanece fonte de verdade.")
            result["failed_ids"].extend(c["id"] for c in chunks if c["id"] not in result["indexed_ids"])
        result["indexed_ids"] = sorted(set(result["indexed_ids"]) - set(result["failed_ids"]))
        result["failed_ids"] = sorted(set(result["failed_ids"]))
        return result

    def search(self, request):
        from weaviate.classes.query import Filter, MetadataQuery
        filters = Filter.by_property("origin").equal("SOURCE")
        for key in ("user_id", "document_id", "work_id", "source_id", "version"):
            filters &= Filter.by_property(key).equal(str(getattr(request, key)))
        limitations = []
        try:
            vector = self.embedding.embed([request.query])[0]
        except ProviderError as exc:
            vector = None
            limitations.append(str(exc))
        with self.connect() as client:
            name = self.collections["SOURCE"]
            if not client.collections.exists(name):
                return {"chunks": [], "limitations": limitations + ["Índice de fontes ainda não criado."]}
            collection = client.collections.use(name)
            if vector:
                found = collection.query.hybrid(query=request.query, vector=vector, alpha=.5,
                    filters=filters, limit=request.limit, return_metadata=MetadataQuery(score=True))
            else:
                found = collection.query.bm25(query=request.query, filters=filters,
                    limit=request.limit, return_metadata=MetadataQuery(score=True))
            chunks = []
            for obj in found.objects:
                props = obj.properties
                props["id"] = props.pop("chunk_id")
                props["vector_id"] = str(obj.uuid)
                props["score"] = obj.metadata.score
                chunks.append(props)
            return {"chunks": chunks, "limitations": limitations}

    def delete_document(self, document_id, user_id):
        from weaviate.classes.query import Filter
        filters = Filter.by_property("user_id").equal(user_id) & Filter.by_property("document_id").equal(document_id)
        deleted = 0
        with self.connect() as client:
            # Include prior embedding generations to honour deletion after model changes.
            names = client.collections.list_all(simple=True)
            for name in names:
                if not name.startswith(("SourceChunks_", "ManuscriptChunks_")):
                    continue
                collection = client.collections.use(name)
                while True:
                    result = collection.data.delete_many(where=filters)
                    deleted += result.successful
                    if result.failed:
                        raise ProviderError("Falha ao excluir objetos vetoriais; repetir reconciliação.")
                    if result.matches <= result.successful:
                        break
        return {"deleted": deleted}

    def ready(self):
        with self.connect() as client:
            if not client.is_ready():
                raise ProviderError("Weaviate indisponível.")
