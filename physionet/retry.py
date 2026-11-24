"""Shared HTTP retry adapter configuration."""

from contextlib import contextmanager
from contextvars import ContextVar
from os import getpid
from threading import RLock
from typing import Iterator, NamedTuple, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


_VERSION_ADAPTER_CACHE_ATTR = "_physionet_version_adapters"
_VERSION_REDIRECT_ARG = "_physionet_version_redirect"


class _VersionLookup(NamedTuple):
    session: requests.Session
    policy: Retry
    request: Optional[requests.PreparedRequest] = None
    adapter: Optional[HTTPAdapter] = None


_version_lookup: ContextVar[Optional[_VersionLookup]] = ContextVar("physionet_version_lookup", default=None)


@contextmanager
def _use_version_lookup(lookup: Optional[_VersionLookup]) -> Iterator[None]:
    token = _version_lookup.set(lookup)
    try:
        yield
    finally:
        _version_lookup.reset(token)


def _adapter_lock(session):
    # Session copies and forked children need their own lock.
    locks = vars(session).setdefault("_physionet_adapter_locks", {})
    pid = getpid()
    lock = locks.get(pid)
    if lock is None:
        lock = locks.setdefault(pid, RLock())
    return lock


class RetrySession(requests.Session):
    """Select and mount adapters safely when project lookups share a session."""

    # Requests copies the mounted adapters. Keep their cache with them.
    __attrs__ = [*requests.Session.__attrs__, _VERSION_ADAPTER_CACHE_ATTR]

    def prepare_request(self, request):
        lookup = _version_lookup.get()
        if lookup is None or lookup.session is not self or lookup.request is not None:
            return super().prepare_request(request)
        # Auth callbacks during preparation can make their own requests.
        with _use_version_lookup(None):
            prepared = super().prepare_request(request)
        _version_lookup.set(lookup._replace(request=prepared))
        return prepared

    def resolve_redirects(self, resp, req, *args, **kwargs):
        lookup = _version_lookup.get()
        if lookup is not None and lookup.session is self and lookup.request is req:
            # Requests passes these kwargs to send() for each redirect.
            kwargs[_VERSION_REDIRECT_ARG] = lookup
        return super().resolve_redirects(resp, req, *args, **kwargs)

    def send(self, request, **kwargs):
        redirected = kwargs.pop(_VERSION_REDIRECT_ARG, None)
        if redirected is None:
            return super().send(request, **kwargs)
        with _use_version_lookup(redirected._replace(request=request)):
            return super().send(request, **kwargs)

    def mount(self, prefix, adapter):
        # Requests reorders its adapter table in place while mounting.
        with _adapter_lock(self):
            super().mount(prefix, adapter)

    def get_adapter(self, url):
        with _adapter_lock(self):
            return super().get_adapter(url)


class _VersionRetryAdapter(HTTPAdapter):
    def send(self, request, *args, **kwargs):
        lookup = _version_lookup.get()
        if lookup is None:
            return super().send(request, *args, **kwargs)
        # A response hook can send another PreparedRequest through this very adapter.
        active = lookup._replace(adapter=self) if lookup.request is request else None
        with _use_version_lookup(active):
            return super().send(request, *args, **kwargs)

    @property
    def max_retries(self):
        # HTTPAdapter.send reads this before handing the request to urllib3.
        configured = self._configured_retries
        lookup = _version_lookup.get()
        if lookup is not None and lookup.adapter is self and lookup.policy is not configured:
            with _adapter_lock(lookup.session):
                adapters = getattr(lookup.session, _VERSION_ADAPTER_CACHE_ATTR, None)
                if adapters and self in adapters.values():
                    return lookup.policy
        return configured

    @max_retries.setter
    def max_retries(self, policy):
        self._configured_retries = policy

    def __getstate__(self):
        # A copy made during a lookup should keep the adapter's persistent policy.
        state = super().__getstate__()
        state["max_retries"] = self._configured_retries
        return state


def retry_policy(retries: int, status_only: bool = False) -> Retry:
    """
    The retry policy for requests that may hit transient errors.

    502, 503 and 504 responses are retried up to `retries` times, with
    backoff. By default, connection errors and timeouts are retried too, as
    for file downloads. With status_only, nothing else is retried: connection
    errors, timeouts and other errors (such as an SSL error) are raised
    straight away, so that being offline or a hung server is reported at once,
    as the usual requests exception, and other responses (such as 429 or 413
    with a Retry-After header) are returned as they are. connect=0 (rather
    than False) keeps that exception a requests ConnectionError rather than a
    raw urllib3 one.
    """
    if status_only:
        return Retry(
            total=retries,
            connect=0,
            read=False,
            other=0,
            backoff_factor=1,
            status_forcelist=[502, 503, 504],
            raise_on_status=False,
            respect_retry_after_header=False,
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
        adapter_type = _VersionRetryAdapter if status_only else HTTPAdapter
        adapter = adapter_type(max_retries=retry_policy(retries, status_only))
        cache[prefix] = adapter
        session.mount(prefix, adapter)
    else:
        # The adapter is dedicated to this URL prefix, so changing its policy
        # does not modify a shared adapter or discard its pool.
        adapter.max_retries = retry_policy(retries, status_only)

    return adapter


@contextmanager
def version_retries(session: requests.Session, prefix: str, retries: int) -> Iterator[None]:
    """Pin this lookup's retry policy while keeping the shared projects pool."""
    with _adapter_lock(session):
        adapter = configure_retry_adapter(
            session, prefix, retries, adapter_prefix=prefix, cache_attr=_VERSION_ADAPTER_CACHE_ATTR, status_only=True
        )
        if isinstance(adapter, _VersionRetryAdapter):
            policy = adapter._configured_retries
        elif retries <= 0:
            # A concurrent lookup can install the adapter after a cold retries=0 call starts.
            policy = Retry(total=0, read=False)
        else:
            policy = retry_policy(retries, status_only=True)
    with _use_version_lookup(_VersionLookup(session, policy)):
        yield
