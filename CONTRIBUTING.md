# Contributing to sartoriuslib

Thanks for your interest. Please read [docs/design.md](docs/design.md) before
making non-trivial changes — most design decisions are already made and
documented there.

## Dev setup

```bash
git clone https://github.com/GraysonBellamy/sartoriuslib
cd sartoriuslib
uv sync --all-extras --dev
uv run pre-commit install
```

## Core checks (must pass before merging)

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
```

## Adding a new Sartorius command

Per the design doc §4.2 and §6, a new command is:

1. One `Command` object in `src/sartoriuslib/commands/<group>.py` with its
   `XbpiVariant` and/or `SbiVariant`, `safety` tier, and `family_hints` /
   `capability_hints` priors.
2. One request dataclass and one response dataclass (frozen, slotted).
3. One facade one-liner on `Balance` — plus a sync-facade one-liner or
   `@sync_version` wrapper.
4. One fixture-backed unit test hitting each variant's `encode(...)` and
   `decode(...)`, plus one `FakeTransport` round-trip test.

**Nothing else.** No hand-written byte paths; no per-command branching in
`Session`.

## Safety

Any command that can damage hardware, lose data, or write EEPROM must set
`safety = SafetyTier.PERSISTENT` or `SafetyTier.DANGEROUS` on its `Command`
spec and accept `confirm=True` at the facade. The `Session` rejects
`confirm is not True` before any I/O. See design doc §6.1.

## Commits

Conventional-style short prefixes are helpful but not mandatory:

- `feat:` new user-visible behaviour
- `fix:` bugfix
- `refactor:` internal cleanup
- `docs:` docs only
- `ci:` pipeline changes
- `chore:` tooling/version bumps

## Tests that need hardware

Mark them with `hardware`, `hardware_stateful`, or `hardware_destructive`.
These are skipped in CI by default. Stateful and destructive tiers also
require opt-in env vars (`SARTORIUSLIB_ENABLE_STATEFUL_TESTS=1`,
`SARTORIUSLIB_ENABLE_DESTRUCTIVE_TESTS=1`).
