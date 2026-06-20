"""News source abstraction.

Implement `fetch()` for each provider. v1 ships an RSS source; a future
Bloomberg / Reuters API source only needs to subclass NewsSource and return
the same NewsEvent shape — nothing downstream changes.
"""
from __future__ import annotations

import abc
from typing import List

from ..models import NewsEvent


class NewsSource(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    async def fetch(self) -> List[NewsEvent]:
        """Return the latest batch of news events (dedup happens downstream)."""
        raise NotImplementedError
