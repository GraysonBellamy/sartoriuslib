# sartoriuslib

Python library for [Sartorius](https://www.sartorius.com/) balances over
serial. Supports both wire protocols the hardware speaks: **xBPI** (binary,
length-prefixed, checksum-protected, SBN-addressed) and **SBI** (ASCII
command/response and autoprint).

This site is the reference for the **v1** design. The authoritative
architectural document lives at [Design](design.md); every design decision in
the library should be traceable to a section there. The library is built as a
sibling to [`alicatlib`](https://github.com/GraysonBellamy/alicatlib) — same
async-first device facade, sync wrapper, manager, fake transport, acquisition
helpers, sinks, typed models, and explicit safety gates.

## Where to start

- [Installation](installation.md)
- [Async quickstart](quickstart-async.md)
- [Sync quickstart](quickstart-sync.md)
- [Balances](devices.md) — `Balance`, families (CUBIS / OEM weigh cell / basic lab), capability flags
- [Commands](commands.md) — the command surface, safety tiers, protocol-variant dispatch
- [Streaming](streaming.md) — xBPI cadenced poll and the three SBI stream modes
- [Logging and acquisition](logging.md) — recorder, sinks, structured log events
- [Safety](safety.md) — destructive operations and `confirm=True`
- [Testing](testing.md) — `FakeTransport`, fixtures, hardware tiers
- [Wire protocol](protocol.md) — xBPI/SBI framing, opcode tables, capture analysis

## Status

Alpha. The library ships the full transport (real + fake), both protocol
clients (xBPI and SBI), the `Balance` facade, multi-device `SartoriusManager`,
recorder and `record(...)` helper, all first-party sinks (CSV, JSONL, SQLite
in the base install; Parquet, Postgres behind extras), the sync facade,
fixture-based testing utilities, and the stable `sarto-*` CLI plus the
`sarto-diag` reverse-engineering namespace. The public architecture is
frozen; documentation completion and broader hardware coverage are in
progress. See [Design](design.md) for forward work.
