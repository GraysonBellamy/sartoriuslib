"""Protocol-client factory — xBPI vs SBI selection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sartoriuslib.protocol.base import ProtocolKind
from sartoriuslib.protocol.sbi.client import SbiProtocolClient
from sartoriuslib.protocol.xbpi.client import XbpiProtocolClient

if TYPE_CHECKING:
    from sartoriuslib.transport.base import Transport

__all__ = ["make_protocol_client"]


def make_protocol_client(
    protocol: ProtocolKind,
    transport: Transport,
    *,
    default_timeout: float = 1.0,
) -> XbpiProtocolClient | SbiProtocolClient:
    """Build the concrete protocol client for ``protocol`` on ``transport``.

    :attr:`AUTO` is never valid at factory time — the detection step
    resolves it first.
    """
    if protocol is ProtocolKind.XBPI:
        return XbpiProtocolClient(transport, default_timeout=default_timeout)
    if protocol is ProtocolKind.SBI:
        return SbiProtocolClient(transport, default_timeout=default_timeout)
    raise ValueError(
        f"cannot build a protocol client for {protocol!r}; "
        "AUTO must resolve to XBPI or SBI before this point",
    )
