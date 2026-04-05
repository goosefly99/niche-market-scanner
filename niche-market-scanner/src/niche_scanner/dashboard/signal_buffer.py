"""In-memory buffer for recent EdgeSignals.

Holds a bounded FIFO of the most recently emitted :class:`EdgeSignal`
objects so that the dashboard's ``/signals`` page and ``/api/signals/recent``
endpoint can display them without hitting the database.  Signals are
volatile — the buffer is process-local and is cleared on restart.

The buffer is thread-safe for the single-event-loop async context used
by the scanner and dashboard (all calls happen on the same loop).

Signals typically flow in from the scan loop:

- ``MarketScanner.scan_cycle`` produces a list of ``EdgeSignal``.
- ``main.py`` appends each newly-detected signal into the buffer via
  :meth:`SignalBuffer.append`.
- The dashboard reads recent signals via :meth:`SignalBuffer.recent`.

Capacity defaults to 100 entries.  Oldest signals are evicted on push
once the buffer is full.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from niche_scanner.engines.base import EdgeSignal

DEFAULT_MAX_SIGNALS = 100


class SignalBuffer:
    """Bounded FIFO buffer of recent EdgeSignals.

    Parameters
    ----------
    maxlen:
        Maximum number of signals to retain.  When full, pushing a new
        signal evicts the oldest.  Defaults to ``DEFAULT_MAX_SIGNALS``.
    """

    def __init__(self, maxlen: int = DEFAULT_MAX_SIGNALS) -> None:
        if maxlen < 1:
            raise ValueError("maxlen must be >= 1")
        self._buffer: deque[EdgeSignal] = deque(maxlen=maxlen)

    @property
    def maxlen(self) -> int:
        """Return the configured capacity of the buffer."""
        assert self._buffer.maxlen is not None
        return self._buffer.maxlen

    def append(self, signal: EdgeSignal) -> None:
        """Push a single signal into the buffer (newest at the end).

        If the buffer is at capacity, the oldest entry is evicted.
        """
        self._buffer.append(signal)

    def extend(self, signals: list[EdgeSignal]) -> None:
        """Push a list of signals into the buffer, preserving order.

        Entries are appended in the order given.  When the buffer is at
        capacity the oldest entries are evicted first.
        """
        self._buffer.extend(signals)

    def recent(self, limit: int = 20) -> list[EdgeSignal]:
        """Return up to ``limit`` most-recent signals, newest first.

        Parameters
        ----------
        limit:
            Maximum number of signals to return.  Must be >= 1.
        """
        if limit < 1:
            raise ValueError("limit must be >= 1")
        # deque is oldest-first; reverse to newest-first and slice
        items = list(self._buffer)
        items.reverse()
        return items[:limit]

    def clear(self) -> None:
        """Remove all signals from the buffer."""
        self._buffer.clear()

    def __len__(self) -> int:
        return len(self._buffer)
