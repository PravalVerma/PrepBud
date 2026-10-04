"""Application exceptions and the global handlers that render the error envelope.

Every error leaves the API as::

    {"error": {"code", "message", "details"}, "meta": {"request_id", "timestamp"}}

with the HTTP status / code pairs defined in API_CONTRACT §2.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger
from app.domain.common import build_meta

logger = get_logger(__name__)


class AppError(Exception):
    status_code: int = 500
    code: str = "INTERNAL_ERROR"
    message: str = "An unexpected error occurred"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details or {}
        self.headers = headers
        super().__init__(self.message)


class ValidationError(AppError):
    status_code = 400
    code = "VALIDATION_ERROR"
    message = "Invalid request"


class AuthenticationError(AppError):
    status_code = 401
    code = "AUTHENTICATION_REQUIRED"
    message = "Authentication required"

    def __init__(self, message: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("headers", {"WWW-Authenticate": "Bearer"})
        super().__init__(message, **kwargs)


class ForbiddenError(AppError):
    status_code = 403
    code = "FORBIDDEN"
    message = "You do not have permission to perform this action"


class NotFoundError(AppError):
    """Raised for missing resources *and* resources owned by another user.

    Returning 404 (never 403) avoids leaking the existence of other users' data.
    """

    status_code = 404
    code = "RESOURCE_NOT_FOUND"
    message = "Resource not found"


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"
    message = "Resource conflict"


class UnprocessableEntityError(AppError):
    status_code = 422
    code = "UNPROCESSABLE_ENTITY"
    message = "Request could not be processed"


class RateLimitedError(AppError):
    status_code = 429
    code = "RATE_LIMITED"
    message = "Too many requests"


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "SERVICE_UNAVAILABLE"
    message = "A required service is unavailable"


_STATUS_TO_CODE: dict[int, str] = {
    400: "VALIDATION_ERROR",
    401: "AUTHENTICATION_REQUIRED",
    403: "FORBIDDEN",
    404: "RESOURCE_NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "UNPROCESSABLE_ENTITY",
    429: "RATE_LIMITED",
    503: "SERVICE_UNAVAILABLE",
}


def error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = {
        "error": {"code": code, "message": message, "details": details or {}},
        "meta": build_meta(request).model_dump(mode="json", exclude_none=True),
    }
    return JSONResponse(status_code=status_code, content=jsonable_encoder(body), headers=headers)


async def _app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    exc = cast(AppError, exc)
    return error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
        headers=exc.headers,
    )


async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    errors = [
        {
            "loc": list(err.get("loc", ())),
            "message": err.get("msg", ""),
            "type": err.get("type", ""),
        }
        for err in cast(RequestValidationError, exc).errors()
    ]
    return error_response(
        request,
        status_code=400,
        code="VALIDATION_ERROR",
        message="Invalid request body or parameters",
        details={"errors": errors},
    )


async def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    exc = cast(StarletteHTTPException, exc)
    fallback = "INTERNAL_ERROR" if exc.status_code >= 500 else "ERROR"
    code = _STATUS_TO_CODE.get(exc.status_code, fallback)
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return error_response(
        request,
        status_code=exc.status_code,
        code=code,
        message=message,
        headers=getattr(exc, "headers", None),
    )


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception", extra={"path": request.url.path})
    return error_response(
        request,
        status_code=500,
        code="INTERNAL_ERROR",
        message="An unexpected error occurred",
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
