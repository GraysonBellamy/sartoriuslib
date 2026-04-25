# Installation

```bash
pip install sartoriuslib
```

Requires **Python 3.13 or newer**. Core dependencies are
[`anyio`](https://pypi.org/project/anyio/) (async runtime) and
[`anyserial`](https://pypi.org/project/anyserial/) (cross-platform serial I/O).

## Optional extras

```bash
pip install 'sartoriuslib[parquet]'    # ParquetSink (pyarrow)
pip install 'sartoriuslib[postgres]'   # PostgresSink (asyncpg)
pip install 'sartoriuslib[docs]'       # build the docs locally
```

CSV, JSONL, SQLite, and InMemory sinks need no extras — they use only the
standard library. See [Logging and acquisition](logging.md) for the sink
surface and [Sinks API](api/sinks.md) for the reference.

## Platform support

Linux, macOS, BSD, and Windows are supported via
[`anyserial`](https://pypi.org/project/anyserial/) (readiness-driven I/O on
POSIX, IOCP on Windows). Serial-port enumeration uses
`anyserial.list_serial_ports()` natively.

On Linux, the user running `sarto-*` commands must have read/write access to
the serial device — usually achieved by adding the user to the `dialout`
group (`sudo usermod -aG dialout $USER`) and re-logging.
