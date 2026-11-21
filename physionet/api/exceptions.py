class PhysioNetAPIError(Exception):
    """Base exception for PhysioNet API errors."""

    def __init__(self, *args, response=None):
        super().__init__(*args)
        # The HTTP response that caused the error, if there was one.
        self.response = response


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
