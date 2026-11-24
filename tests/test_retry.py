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


@pytest.mark.parametrize(("first", "second"), [("My API", "My%20API"), ("My%20API", "My API")])
def test_equivalent_deployment_paths_reuse_the_retry_adapter(first, second):
    with requests.Session() as session:
        initial_mounts = len(session.adapters)
        first_url = f"https://example.org/{first}/one.txt"
        second_url = f"https://example.org/{second}/two.txt"
        adapter = configure_retry_adapter(session, first_url, 2)
        assert session.get_adapter("https://example.org/My%20API/one.txt") is adapter
        pool = adapter.poolmanager
        assert configure_retry_adapter(session, second_url, 1) is adapter
        assert adapter.max_retries.total == 1
        configure_retry_adapter(session, first_url, 0)
        assert adapter.max_retries.total == 0
        assert adapter.max_retries.read is False
        assert adapter.poolmanager is pool
        assert len(session.adapters) == initial_mounts + 1


@pytest.mark.parametrize("subpath", ["", "nested/"], ids=["same-prefix", "more-specific"])
def test_caller_adapter_wins_on_encoded_deployment_path(subpath):
    with requests.Session() as session:
        prefix = "https://example.org/My%20API/"
        caller_adapter = requests.adapters.HTTPAdapter(max_retries=5)
        session.mount(prefix + subpath, caller_adapter)
        configure_retry_adapter(session, "https://example.org/My API/file.txt", 1)
        assert session.get_adapter(prefix + subpath + "file.txt") is caller_adapter
        assert caller_adapter.max_retries.total == 5
