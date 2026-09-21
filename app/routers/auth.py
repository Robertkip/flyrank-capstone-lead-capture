"""Registration and login. Issues the JWT every admin route requires."""

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import current_tenant
from app.errors import AppError
from app.models import Tenant
from app.schemas import LoginRequest, RegisterRequest, TenantOut, TokenResponse
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> TokenResponse:
    existing = db.execute(
        select(Tenant.id).where(Tenant.email == payload.email.lower())
    ).scalar_one_or_none()
    if existing is not None:
        raise AppError("an account with that email already exists", status_code=409)

    tenant = Tenant(
        email=payload.email.lower(),
        name=payload.name,
        password_hash=hash_password(payload.password),
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    token, expires_in = create_access_token(str(tenant.id), tenant.email)
    return TokenResponse(access_token=token, expires_in=expires_in)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    tenant = db.execute(
        select(Tenant).where(Tenant.email == payload.email.lower())
    ).scalar_one_or_none()

    # Same message either way: never reveal which half was wrong.
    if tenant is None or not verify_password(payload.password, tenant.password_hash):
        raise AppError("invalid email or password", status_code=401)

    token, expires_in = create_access_token(str(tenant.id), tenant.email)
    return TokenResponse(access_token=token, expires_in=expires_in)


@router.get("/me", response_model=TenantOut)
def me(tenant: Tenant = Depends(current_tenant)) -> Tenant:
    return tenant
