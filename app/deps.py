"""FastAPI dependencies: authentication and client-IP resolution."""

import uuid

import jwt
from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import AppError
from app.models import Tenant
from app.security import decode_access_token

UNAUTHORIZED_HEADERS = {"WWW-Authenticate": "Bearer"}


def current_tenant(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Tenant:
    """Resolve the caller's tenant from a Bearer JWT, or reject with 401."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AppError("missing or malformed Authorization header", status_code=401)

    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise AppError("token has expired", status_code=401) from None
    except jwt.PyJWTError:
        raise AppError("invalid token", status_code=401) from None

    try:
        tenant_id = uuid.UUID(str(claims.get("sub")))
    except (ValueError, TypeError):
        raise AppError("invalid token subject", status_code=401) from None

    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise AppError("account no longer exists", status_code=401)
    return tenant


def client_ip(request: Request) -> str:
    """Best-effort client IP.

    Behind a proxy the first X-Forwarded-For entry is the client. Trust it only
    because this deployment sits behind a proxy we control; a public deployment
    should validate the proxy chain instead.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
