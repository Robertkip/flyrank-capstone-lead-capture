"""Uniform JSON error shape.

Every failure the client can cause returns ``{"error": ..., "detail": ...}``
with a 4xx status. A 500 means we have a bug, never that input was bad.
"""

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    """Domain error carrying the status code the boundary should return."""

    def __init__(self, message: str, status_code: int = 400, detail=None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.detail = detail


def _json(status_code: int, error: str, detail=None, headers=None) -> JSONResponse:
    body = {"error": error}
    if detail is not None:
        body["detail"] = detail
    return JSONResponse(status_code=status_code, content=body, headers=headers)


def field_name(err: dict) -> str:
    """Human-readable field path for one Pydantic error.

    Unparseable JSON has no field — its ``loc`` is a byte offset, which would
    otherwise surface as a meaningless field named e.g. "30".
    """
    if err.get("type") == "json_invalid":
        return "body"
    return ".".join(str(part) for part in err.get("loc", ())[1:]) or "body"


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return _json(exc.status_code, exc.message, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        detail = [
            {
                "field": field_name(err),
                "message": err.get("msg", "invalid"),
                "type": err.get("type", "value_error"),
            }
            for err in exc.errors()
        ]
        return _json(status.HTTP_422_UNPROCESSABLE_ENTITY, "validation_failed", detail)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        return _json(exc.status_code, str(exc.detail), headers=getattr(exc, "headers", None))
