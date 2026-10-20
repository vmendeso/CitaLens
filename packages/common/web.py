import logging
import secrets
import uuid
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from packages.common.config import get_settings

logger = logging.getLogger("citalens")


def create_app(name: str, internal: bool = True) -> FastAPI:
    app = FastAPI(title=f"Autobibliograph Revision · {name}", version="1.0.0",
                  description="Contratos v1. Conteúdo de documentos é dado não confiável.")

    @app.middleware("http")
    async def envelope(request: Request, call_next):
        correlation_id = str(uuid.uuid4())
        request.state.correlation_id = correlation_id
        if internal and not request.url.path.startswith("/health/"):
            if not secrets.compare_digest(
                request.headers.get("X-Internal-Token", ""), get_settings().internal_token
            ):
                return JSONResponse(status_code=401, headers={
                    "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                    "X-Correlation-ID": correlation_id,
                }, content={
                    "code": "INTERNAL_AUTH_REQUIRED", "message": "Autenticação interna necessária.",
                    "correlation_id": correlation_id,
                })
        try:
            response = await call_next(request)
        except Exception:
            # Never include request bodies, tokens, URLs with credentials or provider responses.
            logger.error("request_failed service=%s correlation_id=%s", name, correlation_id)
            response = JSONResponse(status_code=500, content={
                "code": "INTERNAL_ERROR", "message": "Falha interna. Consulte o identificador do erro.",
                "correlation_id": correlation_id,
            })
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={
            "code": detail.get("code", f"HTTP_{exc.status_code}"),
            "message": detail.get("message", "Não foi possível concluir a operação."),
            "correlation_id": request.state.correlation_id,
            **({"details": detail["details"]} if "details" in detail else {}),
        }, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={
            "code": "VALIDATION_ERROR", "message": "Verifique os dados enviados.",
            "correlation_id": request.state.correlation_id,
            "details": [{"location": list(e["loc"]), "message": e["msg"]} for e in exc.errors()],
        })

    @app.get("/health/live", tags=["health"])
    def live():
        return {"status": "ok", "service": name}

    @app.get("/health/ready", tags=["health"])
    def ready():
        probe = getattr(app.state, "readiness", None)
        if probe:
            probe()
        return {"status": "ready", "service": name}

    return app
