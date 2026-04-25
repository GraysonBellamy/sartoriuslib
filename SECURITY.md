# Security policy

## Reporting a vulnerability

Please email [gbellamy@umd.edu](mailto:gbellamy@umd.edu) or open a private
security advisory on GitHub:
<https://github.com/GraysonBellamy/sartoriuslib/security/advisories/new>.

Do **not** file public issues for security reports.

## Scope

`sartoriuslib` drives physical equipment over serial. Please report:

- Code paths that send `PERSISTENT` or `DANGEROUS` commands without
  `confirm=True`.
- Any path that logs credentials, DSNs, or secrets (`PostgresConfig.password`
  in particular is a non-logging field).
- SQL-injection surfaces in `PostgresSink`.
- Deserialisation of untrusted input in fixture loaders.
- Protocol-mode switches that run as a side effect of `open_device(...)` —
  these should only be reachable via `configure_protocol(..., confirm=True)`
  or the `sartoriuslib.maintenance` module.
