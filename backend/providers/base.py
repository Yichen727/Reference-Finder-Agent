from collections.abc import Callable
from typing import Protocol

from backend.schemas.models import SearchOutcome, SearchStrategy, StoredBrief

ProgressCallback = Callable[[str], None]


class ReferenceProvider(Protocol):
    def strategy(self, brief: StoredBrief) -> SearchStrategy: ...

    def search(
        self, brief: StoredBrief, strategy: SearchStrategy, progress: ProgressCallback | None = None
    ) -> SearchOutcome: ...
