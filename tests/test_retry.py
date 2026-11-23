"""Tests for the shared retry policy and adapters."""

import pytest
import requests
from urllib3.exceptions import MaxRetryError, SSLError

from physionet.retry import configure_retry_adapter, retry_policy


def test_status_only_policy_does_not_retry_connection_errors_or_timeouts():
    policy = retry_policy(3, status_only=True)
    assert (policy.total, policy.connect, policy.read, policy.other) == (3, 0, False, 0)
    assert set(policy.status_forcelist) == {502, 503, 504}
    assert policy.raise_on_status is False


@pytest.mark.parametrize("status", [502, 503, 504])
def test_status_only_policy_retries_transient_statuses(status):
    policy = retry_policy(3, status_only=True)
    assert policy.is_retry("GET", status, has_retry_after=False)
    assert policy.is_retry("GET", status, has_retry_after=True)


@pytest.mark.parametrize("status", [413, 429, 500, 404])
def test_status_only_policy_does_not_retry_other_statuses(status):
    """Not even with a Retry-After header, which urllib3 honours by default."""
    policy = retry_policy(3, status_only=True)
    assert not policy.is_retry("GET", status, has_retry_after=True)


def test_status_only_policy_raises_other_errors_at_once():
    policy = retry_policy(3, status_only=True)
    with pytest.raises(MaxRetryError):
        policy.increment(method="GET", url="/", error=SSLError("certificate verify failed"))


def test_default_policy_retries_connection_errors_too():
    policy = retry_policy(3)
    assert (policy.total, policy.connect, policy.read) == (3, None, None)
    assert set(policy.status_forcelist) == {502, 503, 504}


def test_new_and_reused_adapters_get_the_same_policy():
    """The policy is built in one place (retry_policy), for new and reused adapters."""
    session = requests.Session()
    for status_only in (False, True):
        options = {"cache_attr": f"_test_{status_only}", "status_only": status_only}
        new = configure_retry_adapter(session, "https://example.org/a/", 3, **options)
        reused = configure_retry_adapter(session, "https://example.org/a/", 3, **options)
        assert reused is new
        expected = retry_policy(3, status_only)
        names = ("total", "connect", "read", "other", "status_forcelist", "raise_on_status", "backoff_factor")
        names += ("respect_retry_after_header",)
        for name in names:
            assert getattr(new.max_retries, name) == getattr(expected, name)
