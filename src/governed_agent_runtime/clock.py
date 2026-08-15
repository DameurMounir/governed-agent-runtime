"""Injectable clocks keep decisions and evidence deterministic in tests."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Clock interface consumed by the runtime."""

    def now(self) -> datetime:
        """Return an aware UTC-compatible timestamp."""


@dataclass(frozen=True, slots=True)
class SystemClock:
    """Production wall clock."""

    def now(self) -> datetime:
        """Return the current UTC timestamp."""

        return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class FrozenClock:
    """Fixed clock for deterministic execution and testing."""

    instant: datetime

    def __post_init__(self) -> None:
        if self.instant.tzinfo is None or self.instant.utcoffset() is None:
            raise ValueError("FrozenClock instant must be timezone-aware")

    def now(self) -> datetime:
        """Return the configured instant."""

        return self.instant
