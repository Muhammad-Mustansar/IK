import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

class AppException(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: Any = None,
    ) -> None:
        self.message = message
        self.status_code = status_code
        self.details = details
        super().__init__(message)


class NotFoundError(AppException):
    def __init__(self, message: str = "Resource not found", *, details: Any = None) -> None:
        super().__init__(message, status_code=status.HTTP_404_NOT_FOUND, details=details)


class UnauthorizedError(AppException):
    def __init__(self, message: str = "Unauthorized", *, details: Any = None) -> None:
        super().__init__(message, status_code=status.HTTP_401_UNAUTHORIZED, details=details)


class ForbiddenError(AppException):
    def __init__(self, message: str = "Forbidden", *, details: Any = None) -> None:
        super().__init__(message, status_code=status.HTTP_403_FORBIDDEN, details=details)


class ConflictError(AppException):
    def __init__(self, message: str = "Conflict", *, details: Any = None) -> None:
        super().__init__(message, status_code=status.HTTP_409_CONFLICT, details=details)


class BadRequestError(AppException):
    def __init__(self, message: str = "Bad request", *, details: Any = None) -> None:
        super().__init__(message, status_code=status.HTTP_400_BAD_REQUEST, details=details)


class InsufficientStockError(AppException):
    def __init__(self, message: str = "Insufficient stock", *, details: Any = None) -> None:
        super().__init__(message, status_code=status.HTTP_400_BAD_REQUEST, details=details)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def app_exception_handler(_: Request, exc: AppException) -> JSONResponse:
        content: dict[str, Any] = {"detail": exc.message}
        if exc.details is not None:
            content["details"] = exc.details
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"},
        )
