from __future__ import annotations

from datetime import datetime

import pytest

from governed_agent_runtime.clock import FrozenClock


def test_frozen_clock_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FrozenClock(datetime(2026, 1, 1))  # noqa: DTZ001
