"""Shared HTTP retry adapter configuration."""

from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def configure_retry_adapter(
    session: requests.Session,
    url: str,
    retries: int,
    adapter_prefix: Optional[str] = None,
    cache_attr: str = "_physionet_retry_adapters",
) -> Optional[HTTPAdapter]:
    """Mount and cache a retry adapter scoped to a URL prefix."""
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
        adapter = HTTPAdapter(
            max_retries=Retry(
                total=retries,
                backoff_factor=1,
                status_forcelist=[502, 503, 504],
                raise_on_status=False,
            )
        )
        cache[prefix] = adapter
        session.mount(prefix, adapter)
    else:
        # The adapter is dedicated to this URL prefix, so changing its policy
        # does not modify a shared adapter or discard its pool.
        adapter.max_retries = Retry(
            total=retries,
            backoff_factor=1,
            status_forcelist=[502, 503, 504],
            raise_on_status=False,
        )

    return adapter
