"""Tests for the shared retry policy and adapters."""

import requests

from physionet.retry import configure_retry_adapter, retry_policy


def test_status_only_policy_does_not_retry_connection_errors_or_timeouts():
    policy = retry_policy(3, status_only=True)
    assert (policy.total, policy.connect, policy.read) == (3, 0, False)
    assert set(policy.status_forcelist) == {502, 503, 504}
    assert policy.raise_on_status is False


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
        for name in ("total", "connect", "read", "status_forcelist", "raise_on_status", "backoff_factor"):
            assert getattr(new.max_retries, name) == getattr(expected, name)
