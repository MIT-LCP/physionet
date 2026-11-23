"""Shared HTTP retry adapter configuration."""

from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def retry_policy(retries: int, status_only: bool = False) -> Retry:
    """
    The retry policy for requests that may hit transient errors.

    502, 503 and 504 responses are retried up to `retries` times, with
    backoff. By default, connection errors and timeouts are retried too, as
    for file downloads. With status_only, they are raised straight away, so
    that being offline or a hung server is reported at once, as the usual
    requests exception. connect=0 (rather than False) keeps that exception a
    requests ConnectionError rather than a raw urllib3 one.
    """
    if status_only:
        return Retry(
            total=retries,
            connect=0,
            read=False,
            backoff_factor=1,
            status_forcelist=[502, 503, 504],
            raise_on_status=False,
        )
    return Retry(
        total=retries,
        backoff_factor=1,
        status_forcelist=[502, 503, 504],
        raise_on_status=False,
    )


def configure_retry_adapter(
    session: requests.Session,
    url: str,
    retries: int,
    adapter_prefix: Optional[str] = None,
    cache_attr: str = "_physionet_retry_adapters",
    status_only: bool = False,
) -> Optional[HTTPAdapter]:
    """Mount and cache a retry adapter scoped to a URL prefix, using retry_policy()."""
    prefix = (adapter_prefix or url.rsplit("/", 1)[0]).rstrip("/") + "/"
    cache = getattr(session, cache_attr, None)
    if retries <= 0:
        if isinstance(cache, dict):
            adapter = cache.get(prefix)
            if isinstance(adapter, HTTPAdapter):
                adapter.max_retries = Retry(total=0, read=False)
        return None

    if not isinstance(cache, dict):
        cache = {}
        setattr(session, cache_attr, cache)

    adapter = cache.get(prefix)
    if adapter is None:
        adapter = HTTPAdapter(max_retries=retry_policy(retries, status_only))
        cache[prefix] = adapter
        session.mount(prefix, adapter)
    else:
        # The adapter is dedicated to this URL prefix, so changing its policy
        # does not modify a shared adapter or discard its pool.
        adapter.max_retries = retry_policy(retries, status_only)

    return adapter
