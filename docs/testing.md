# Testing

`sartoriuslib` ships first-class testing utilities so callers can build
deterministic unit tests against the full balance facade — no hardware
required. Everything in this guide lives in
[`sartoriuslib.testing`](api/testing.md), a public module re-exported
from the top-level package as `from sartoriuslib.testing import ...`.

See [Design](design.md) §8.2.

## Running the test suite

```bash
# Fast unit tests — no hardware, no slow markers.
pytest

# With coverage.
pytest --cov=sartoriuslib --cov-report=term-missing
```

Async tests use [AnyIO's pytest plugin](https://anyio.readthedocs.io/en/stable/testing.html);
mark coroutine tests with `@pytest.mark.anyio`. The suite parametrises
`anyio_backend` to cover both `asyncio` and `trio`.

## Hardware test tiers

The suite uses three pytest markers for hardware-dependent tests. All
three are excluded from the default run; opt in with the matching env
vars.

| Marker | What it does | Opt-in |
| --- | --- | --- |
| `hardware` | Read-only against a connected balance (identify, poll, status). | `SARTORIUSLIB_TEST_PORT=/dev/ttyUSB0` |
| `hardware_stateful` | Changes device state (tare, zero, parameter writes). | `SARTORIUSLIB_ENABLE_STATEFUL_TESTS=1` |
| `hardware_destructive` | Destructive ops (calibration, baud/SBN change, protocol switch). | `SARTORIUSLIB_ENABLE_DESTRUCTIVE_TESTS=1` |

## `FakeTransport` — the canonical test double

[`FakeTransport`](../src/sartoriuslib/transport/fake.py) implements
the full [`Transport`](api/transport.md) protocol against an in-process
script. The script maps write payloads to scripted replies; every
write is recorded so tests can assert exactly what bytes the session
produced.

```python
from sartoriuslib.testing import FakeTransport, canned_frames

async def test_poll_returns_reading() -> None:
    transport = FakeTransport({
        canned_frames.TX_READ_NET: canned_frames.RX_NET_199G,
    })
    async with await open_device(transport) as bal:
        reading = await bal.poll()
    assert reading.value == pytest.approx(199.995)
    assert transport.writes == [canned_frames.TX_READ_NET]
```

`FakeTransport` is constructed with:

- `script: Mapping[bytes, ScriptedReply]` — the write→reply table.
  `ScriptedReply` is `bytes | Sequence[bytes] | Callable[[bytes], bytes | Sequence[bytes]]`,
  so a script entry can be a static reply, a list of frames, or a
  callable that inspects the actual write.
- `label: str` — identifier used in error contexts.
- `latency_s: float` — per-op artificial delay for simulating a slow
  device.

Pass the transport directly to `open_device(transport)` — when `port` is
already a `Transport`, the factory skips serial setup and uses the
transport as-is.

## `canned_frames` — real wire frames

[`canned_frames`](../src/sartoriuslib/testing.py) is a singleton of
`CannedFrames` carrying byte-accurate TX/RX frames synthesised against
the same checksum logic the protocol uses.

```python
from sartoriuslib.testing import canned_frames

# TX: host→balance command frames (assembled via build_command).
canned_frames.TX_READ_NET       # b"\x04\x01\x09\x1e\x2c"
canned_frames.TX_TARE
canned_frames.TX_ZERO
canned_frames.TX_READ_STATUS_BLOCK
canned_frames.TX_READ_MODEL
# ...
```

RX frames carry checksums validated by
`sartoriuslib.protocol.xbpi.checksum`; the canned net-weight frame matches
the worked example in [Wire protocol](protocol.md#33-worked-examples).

## Script builders

For the common identity/metrology/parameter dance, the testing module
ships builders that return ready-to-use `script=` mappings:

| Builder | Returns | Use |
| --- | --- | --- |
| `build_identify_script(...)` | xBPI identity replies | drive `Balance.identify()` end-to-end |
| `build_metrology_script(...)` | `0x0C` / `0x0D` replies | exercise `capacity` / `increment` |
| `build_temperature_script(...)` | `0x76` replies for one or more sensors | exercise `temperature(sensor=N)` |
| `build_parameter_read_script(...)` | `0x55` reply for one index | exercise `read_parameter` |
| `build_parameter_write_script(...)` | `0x56` ack | exercise `write_parameter` + safety gate |
| `build_sbi_identify_script(...)` | SBI `x1_` / `x2_` / `x3_` replies | drive `Balance.identify()` over SBI |

These builders compose — merge their dicts to script multiple commands
in one transport.

## Fixture files

`parse_xbpi_fixture(text)` and `parse_sbi_fixture(text)` parse the
canonical wire-trace fixture format into `script=` mappings.

xBPI fixture (hex tokens, whitespace-tolerant):

```
# xBPI fixture: read net weight
> 04 01 09 1e 2c
< 0b 41 48 bb a3 d7 0a 3d 30 82 45 07
```

SBI fixture (ASCII; reply lines auto-terminate with `\r\n` if missing):

```
# SBI fixture: print
> ESC P
< +     0.00 g
```

Rules:

- Blank lines and `#`-comment lines are ignored.
- `>` lines carry TX bytes (host→balance).
- `<` lines attach to the most recent `>` line.
- Multiple `<` lines after one `>` concatenate into one reply blob — useful
  for synthesising multi-frame replies.
- Malformed input raises `SartoriusValidationError` with the line number.

The captures shipped under `tests/fixtures/` use this format. Convert a
real wire-trace capture by hand or via `sarto-decode` (see
[Troubleshooting](troubleshooting.md)).

## Property-based testing

The dev dependency group includes [Hypothesis](https://hypothesis.readthedocs.io/).
The package ships strategies for `Reading`, `BalanceStatus`, and the
parameter table where useful — see `tests/strategies/` for examples.

## See also

- [Testing API](api/testing.md) — full reference.
- [Transport API](api/transport.md) — `Transport` protocol shape.
- [Design](design.md) §8.2 — fixture format and test layout.
- [Wire protocol](protocol.md) — opcodes / SBI tokens behind the canned frames.
