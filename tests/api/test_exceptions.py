import pytest
from physionet.api.exceptions import (
    PhysioNetAPIError,
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    RateLimitError,
)


def test_base_exception():
    """Test base PhysioNetAPIError exception."""
    with pytest.raises(PhysioNetAPIError):
        raise PhysioNetAPIError("Test error")


def test_bad_request_error():
    """Test BadRequestError is a subclass of PhysioNetAPIError."""
    with pytest.raises(PhysioNetAPIError):
        raise BadRequestError("Bad request")


def test_forbidden_error():
    """Test ForbiddenError is a subclass of PhysioNetAPIError."""
    with pytest.raises(PhysioNetAPIError):
        raise ForbiddenError("Forbidden")


def test_not_found_error():
    """Test NotFoundError is a subclass of PhysioNetAPIError."""
    with pytest.raises(PhysioNetAPIError):
        raise NotFoundError("Not found")


def test_rate_limit_error():
    """Test RateLimitError is a subclass of PhysioNetAPIError."""
    with pytest.raises(PhysioNetAPIError):
        raise RateLimitError("Rate limit exceeded")


def test_exception_messages():
    """Test that exception messages are preserved."""
    error_msg = "Custom error message"

    try:
        raise BadRequestError(error_msg)
    except BadRequestError as e:
        assert str(e) == error_msg
