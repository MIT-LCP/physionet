class PhysioNetAPIError(Exception):
    """Base exception for PhysioNet API errors."""

    pass


class BadRequestError(PhysioNetAPIError):
    """Raised when API returns 400 Bad Request."""

    pass


class ForbiddenError(PhysioNetAPIError):
    """Raised when API returns 403 Forbidden."""

    pass


class NotFoundError(PhysioNetAPIError):
    """Raised when API returns 404 Not Found."""

    pass


class RateLimitError(PhysioNetAPIError):
    """Raised when API returns 429 Too Many Requests."""

    pass
