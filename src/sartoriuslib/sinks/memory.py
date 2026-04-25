r"""In-memory sink — collects :class:`Sample`\ s in a list for tests.

:class:`InMemorySink` satisfies the
:class:`~sartoriuslib.sinks.base.SampleSink` Protocol so acquisition
tests can run the same ``pipe()`` call path a production sink uses
and then inspect the captured samples after the fact.

Design reference: ``docs/design.md`` §10.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from sartoriuslib.streaming.sample import Sample

__all__ = ["InMemorySink"]


class InMemorySink:
    """Collect every written :class:`Sample` in a single list.

    :attr:`samples` is appended to (never re-assigned). :meth:`close`
    does *not* clear the buffer — the point of this sink is post-run
    inspection.
    """

    def __init__(self) -> None:
        self._samples: list[Sample] = []
        self._open = False
        self._closed = False

    @property
    def samples(self) -> list[Sample]:
        """Captured samples, in write order."""
        return self._samples

    @property
    def is_open(self) -> bool:
        """``True`` once :meth:`open` has been called and ``close`` has not."""
        return self._open and not self._closed

    async def open(self) -> None:
        """No backing resource — just flips the open flag."""
        self._open = True
        self._closed = False

    async def write_many(self, samples: Sequence[Sample]) -> None:
        """Append every sample to the internal buffer."""
        if not self.is_open:
            raise RuntimeError("InMemorySink: write_many called before open()")
        self._samples.extend(samples)

    async def close(self) -> None:
        """Flip the closed flag — no I/O, buffer preserved for inspection."""
        self._closed = True

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        await self.close()
