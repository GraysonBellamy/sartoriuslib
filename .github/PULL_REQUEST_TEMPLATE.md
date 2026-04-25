## Summary

<!-- What changes and why. Link to the design section it realises, if any. -->

## Scope

- [ ] Touches a public API surface
- [ ] Touches transport or protocol layer (xBPI / SBI)
- [ ] Adds or changes a Command (includes new `XbpiVariant` / `SbiVariant`)
- [ ] Changes a safety tier or capability gate

## Test plan

- [ ] `uv run pytest` green locally
- [ ] `uv run ruff check .` clean
- [ ] `uv run mypy` clean (no new ignores)
- [ ] New behaviour has a fixture-backed test (no hardware required)
- [ ] Hardware-only tests marked (`hardware`, `hardware_stateful`, `hardware_destructive`)

## Safety checklist (device control changes only)

- [ ] `PERSISTENT` / `DANGEROUS` ops require `confirm=True` before I/O
- [ ] Typed setters validate input enums before I/O
- [ ] No new silent fallbacks on capability failure
- [ ] Protocol-mode switching remains off the `open_device(...)` path
