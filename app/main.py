"""Application entrypoint: middleware, routes, and the worker lifespan."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.errors import register_error_handlers
from app.outbox import OutboxWorker
from app.routers import auth, dashboard, health, public, widgets

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("app")

worker = OutboxWorker()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.outbox_worker_enabled:
        worker.start()
    try:
        yield
    finally:
        await worker.stop()


app = FastAPI(
    title="Embeddable Widget & Lead-Capture Platform",
    description=(
        "Customers define widgets, embed them with one <script> tag, and this API "
        "safely catches what the public internet submits back."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# The customer sites embedding a widget are arbitrary origins we do not know in
# advance, so the public surface allows any origin. Credentials stay off: auth
# is a Bearer token, never a cookie, so there is nothing for a hostile site to
# ride on. Per-widget origin allow-lists are enforced in the route handlers.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "Idempotency-Key", "If-None-Match"],
    expose_headers=["ETag", "X-RateLimit-Limit", "X-RateLimit-Remaining", "Retry-After"],
    max_age=600,
)


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    """Reject oversized bodies with 413 before anything tries to parse them."""
    if request.method in ("POST", "PATCH", "PUT"):
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > settings.max_body_bytes:
                    logger.warning(
                        "request.payload_too_large path=%s bytes=%s", request.url.path, content_length
                    )
                    return JSONResponse(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        content={
                            "error": "payload_too_large",
                            "detail": f"body must be at most {settings.max_body_bytes} bytes",
                        },
                        headers={"Access-Control-Allow-Origin": "*"},
                    )
            except ValueError:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"error": "invalid Content-Length header"},
                    headers={"Access-Control-Allow-Origin": "*"},
                )
    return await call_next(request)


register_error_handlers(app)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(widgets.router)
app.include_router(public.router)
app.include_router(dashboard.router)


@app.get("/", include_in_schema=False)
def index() -> dict:
    return {
        "service": "Embeddable Widget & Lead-Capture Platform",
        "docs": "/docs",
        "health": "/healthz",
        "widget_bundle": f"/embed/widget.{settings.widget_bundle_version}.js",
    }
