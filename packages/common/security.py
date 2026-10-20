import time
import uuid
from collections import defaultdict, deque
from datetime import timedelta
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from packages.common.config import get_settings
from packages.common.db import get_db
from packages.common.models import User, now

passwords = PasswordHasher()
bearer = HTTPBearer(auto_error=False)
attempts = defaultdict(deque)


def rate_limit_auth(request: Request):
    # Per-process guard; local deployment uses a single API process.
    key = request.client.host if request.client else "unknown"
    bucket = attempts[key]
    current = time.monotonic()
    while bucket and current - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= 20:
        raise HTTPException(429, {"code": "RATE_LIMIT", "message": "Aguarde um minuto e tente novamente."})
    bucket.append(current)


def verify_password(encoded, password):
    try:
        return passwords.verify(encoded, password)
    except VerificationError:
        return False


def access_token(user):
    settings = get_settings()
    return jwt.encode({
        "sub": str(user.id), "iat": now(),
        "exp": now() + timedelta(minutes=settings.jwt_expiration_minutes),
        "iss": "citalens", "aud": "citalens-api",
    }, settings.jwt_secret, algorithm="HS256")


def current_user(auth: HTTPAuthorizationCredentials | None = Depends(bearer), db=Depends(get_db)):
    try:
        if auth is None:
            raise ValueError()
        payload = jwt.decode(auth.credentials, get_settings().jwt_secret, algorithms=["HS256"],
                             issuer="citalens", audience="citalens-api",
                             options={"require": ["exp", "iat", "sub"]})
        user = db.scalar(select(User).where(User.id == uuid.UUID(payload["sub"])))
        if user is None:
            raise ValueError()
        return user
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(401, {"code": "AUTH_REQUIRED", "message": "Entre novamente para continuar."})
