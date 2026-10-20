"""Storage port: replace LocalStorage with an implementation of this protocol for S3."""
from pathlib import Path
from typing import Protocol
import os
import uuid


class Storage(Protocol):
    def put(self, key: str, content: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class LocalStorage:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key):
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError("Invalid object key")
        return path

    def put(self, key, content):
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(f".{uuid.uuid4()}.tmp")
        with temp.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)

    def get(self, key):
        return self.path(key).read_bytes()

    def delete(self, key):
        self.path(key).unlink(missing_ok=True)
