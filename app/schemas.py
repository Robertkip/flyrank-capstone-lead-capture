"""Pydantic models — validation at the HTTP boundary.

Anything that fails here becomes a clean 4xx before business logic runs.
"""

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.config import settings

WIDGET_TYPES = ("signup_form", "cta", "popover")
FIELD_TYPES = ("text", "email", "tel", "textarea", "number", "url")
FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


# --------------------------------------------------------------------------- auth
class RegisterRequest(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    name: str
    created_at: datetime


# ------------------------------------------------------------------------- widget
class WidgetField(BaseModel):
    """One input rendered by the widget and validated on submission."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=40)
    label: str = Field(min_length=1, max_length=120)
    type: Literal[FIELD_TYPES] = "text"  # type: ignore[valid-type]
    required: bool = False
    placeholder: str | None = Field(default=None, max_length=120)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not FIELD_NAME_RE.match(v):
            raise ValueError(
                "field name must be lowercase letters, digits or underscores, starting with a letter"
            )
        if v == settings.honeypot_field:
            raise ValueError(f"'{v}' is reserved for spam protection")
        return v


class WidgetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    type: Literal[WIDGET_TYPES] = "signup_form"  # type: ignore[valid-type]
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    button_text: str = Field(default="Submit", min_length=1, max_length=100)
    success_message: str = Field(default="Thanks! We'll be in touch.", max_length=300)
    fields: list[WidgetField] = Field(min_length=1, max_length=12)
    display: dict[str, Any] = Field(default_factory=dict)
    allowed_origins: list[str] = Field(default_factory=list, max_length=20)
    notify_webhook_url: str | None = Field(default=None, max_length=500)
    notify_email: EmailStr | None = None
    active: bool = True

    @field_validator("fields")
    @classmethod
    def _unique_names(cls, v: list[WidgetField]) -> list[WidgetField]:
        names = [f.name for f in v]
        if len(names) != len(set(names)):
            raise ValueError("field names must be unique")
        return v

    @field_validator("allowed_origins")
    @classmethod
    def _origins_look_like_origins(cls, v: list[str]) -> list[str]:
        for origin in v:
            if not re.match(r"^https?://[^/\s]+$", origin):
                raise ValueError(f"'{origin}' is not a scheme://host[:port] origin")
        return v


class WidgetUpdate(BaseModel):
    """Partial update. Every field optional; unset fields are left alone."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    type: Literal[WIDGET_TYPES] | None = None  # type: ignore[valid-type]
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    button_text: str | None = Field(default=None, min_length=1, max_length=100)
    success_message: str | None = Field(default=None, max_length=300)
    fields: list[WidgetField] | None = Field(default=None, min_length=1, max_length=12)
    display: dict[str, Any] | None = None
    allowed_origins: list[str] | None = Field(default=None, max_length=20)
    notify_webhook_url: str | None = Field(default=None, max_length=500)
    notify_email: EmailStr | None = None
    active: bool | None = None


class WidgetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    public_id: str
    name: str
    type: str
    title: str
    description: str | None
    button_text: str
    success_message: str
    fields: list[dict[str, Any]]
    display: dict[str, Any]
    allowed_origins: list[str]
    notify_webhook_url: str | None
    notify_email: str | None
    active: bool
    config_version: int
    created_at: datetime
    updated_at: datetime
    embed_snippet: str = ""
    config_url: str = ""


# ------------------------------------------------------------------- public config
class PublicFieldConfig(BaseModel):
    name: str
    label: str
    type: str
    required: bool
    placeholder: str | None = None


class PublicWidgetConfig(BaseModel):
    """The small payload the widget script fetches. No tenant data leaks here."""

    id: str
    version: int
    type: str
    title: str
    description: str | None
    button_text: str
    success_message: str
    fields: list[PublicFieldConfig]
    display: dict[str, Any]
    honeypot_field: str
    submit_url: str


# --------------------------------------------------------------------- submission
class SubmissionCreate(BaseModel):
    """The public payload. ``extra="forbid"`` keeps junk out of the database."""

    model_config = ConfigDict(extra="forbid")

    widget_id: str = Field(min_length=6, max_length=32)
    data: dict[str, Any] = Field(default_factory=dict)
    # Hidden field: humans leave it empty, bots fill it.
    honeypot: str | None = Field(default=None, max_length=200)
    # Milliseconds the form was on screen; implausibly fast fills are bots.
    elapsed_ms: int | None = Field(default=None, ge=0, le=86_400_000)

    @field_validator("data")
    @classmethod
    def _bounded_data(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(v) > 25:
            raise ValueError("too many fields in data (max 25)")
        for key, value in v.items():
            if not isinstance(key, str) or len(key) > 40:
                raise ValueError("data keys must be strings of at most 40 characters")
            if isinstance(value, str) and len(value) > settings.max_field_length:
                raise ValueError(
                    f"value for '{key}' exceeds {settings.max_field_length} characters"
                )
            if isinstance(value, (dict, list)):
                raise ValueError(f"value for '{key}' must be a scalar, not a nested structure")
        return v


class SubmissionAccepted(BaseModel):
    status: Literal["ok"] = "ok"
    id: uuid.UUID | None = None
    message: str


class SubmissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    widget_id: uuid.UUID
    data: dict[str, Any]
    country: str | None
    country_code: str | None
    region: str | None
    city: str | None
    geo_status: str
    geo_provider: str | None
    created_at: datetime


class SubmissionPage(BaseModel):
    items: list[SubmissionOut]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------- dashboard
class TimeBucket(BaseModel):
    date: str
    count: int


class GeoBucket(BaseModel):
    country: str | None
    country_code: str | None
    count: int


class WidgetStats(BaseModel):
    widget_id: uuid.UUID
    public_id: str
    name: str
    total_submissions: int
    submissions_last_7_days: int
    spam_blocked: int
    by_day: list[TimeBucket]
    by_country: list[GeoBucket]


class DashboardSummary(BaseModel):
    total_widgets: int
    active_widgets: int
    total_submissions: int
    submissions_last_24h: int
    submissions_last_7_days: int
    spam_blocked: int
    enrichment_success_rate: float
    per_widget: list[dict[str, Any]]


class ErrorResponse(BaseModel):
    error: str
    detail: Any | None = None
