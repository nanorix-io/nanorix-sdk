"""
Stripe-pattern cursor pagination — sync + async iterators.

Used by `client.usage.list_items()` (Gap 13). The API surface is
`GET /v1/usage/items?starting_after=<id>&limit=N` returning
`{object: "list", data: [...], has_more: bool}`. SDK consumers iterate
pages via `for item in client.usage.list_items(): ...` without
paging manually.

Design notes:
- One page = one HTTP roundtrip; auto-pagination chains pages via the
  last record's `id` as the next `starting_after`.
- `limit` is per-page (clamped server-side to 1..=100); total iteration
  is unbounded.
- Async variant requires `async for` semantics; cannot reuse the sync
  iterator's `__iter__` because it would block the event loop.
- Generic `T` is the row type; no constraint beyond serde-able dict.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Callable, Dict, Generic, Iterator, List, Optional, TypeVar

T = TypeVar("T")


class SyncPage(Generic[T]):
    """
    Synchronous iterator over Stripe-pattern cursor-paginated resources.

    Usage:
        for item in client.usage.list_items(limit=100):
            process(item)

    The iterator transparently fetches additional pages as needed by
    setting `starting_after = <last_id>` on each subsequent request.
    Stops when the server signals `has_more=false`.
    """

    def __init__(
        self,
        *,
        fetch_page: Callable[[Optional[str], int], Dict[str, Any]],
        parse_item: Callable[[Dict[str, Any]], T],
        limit: int = 100,
    ) -> None:
        self._fetch_page = fetch_page
        self._parse_item = parse_item
        self._limit = limit
        self._buffer: List[T] = []
        self._raw_buffer: List[Dict[str, Any]] = []
        self._cursor: Optional[str] = None
        self._has_more = True
        self._first_fetch = True

    def __iter__(self) -> Iterator[T]:
        return self

    def __next__(self) -> T:
        if not self._buffer and (self._has_more or self._first_fetch):
            self._fetch_next()
        if not self._buffer:
            raise StopIteration
        return self._buffer.pop(0)

    def _fetch_next(self) -> None:
        page = self._fetch_page(self._cursor, self._limit)
        data = page.get("data", [])
        self._raw_buffer = list(data)
        self._buffer = [self._parse_item(d) for d in data]
        self._has_more = bool(page.get("has_more", False))
        if data:
            self._cursor = data[-1].get("id")
        self._first_fetch = False


class AsyncPage(Generic[T]):
    """
    Asynchronous iterator over Stripe-pattern cursor-paginated resources.

    Usage:
        async for item in client.usage.list_items(limit=100):
            await process(item)
    """

    def __init__(
        self,
        *,
        fetch_page: Callable[[Optional[str], int], Any],
        parse_item: Callable[[Dict[str, Any]], T],
        limit: int = 100,
    ) -> None:
        self._fetch_page = fetch_page
        self._parse_item = parse_item
        self._limit = limit
        self._buffer: List[T] = []
        self._cursor: Optional[str] = None
        self._has_more = True
        self._first_fetch = True

    def __aiter__(self) -> AsyncIterator[T]:
        return self

    async def __anext__(self) -> T:
        if not self._buffer and (self._has_more or self._first_fetch):
            await self._fetch_next()
        if not self._buffer:
            raise StopAsyncIteration
        return self._buffer.pop(0)

    async def _fetch_next(self) -> None:
        page = await self._fetch_page(self._cursor, self._limit)
        data = page.get("data", [])
        self._buffer = [self._parse_item(d) for d in data]
        self._has_more = bool(page.get("has_more", False))
        if data:
            self._cursor = data[-1].get("id")
        self._first_fetch = False
