from functools import lru_cache

from packages.common.config import get_settings
from packages.common.web import create_app
from packages.contracts.assessment import ResolveRequest
from packages.providers.bibliography import SourceResolver

app = create_app("web-search-service")


@lru_cache
def resolver():
    return SourceResolver(get_settings())


@app.post("/resolve")
def resolve(request: ResolveRequest):
    return resolver().resolve(request)
