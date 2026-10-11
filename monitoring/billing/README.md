# Billing monitor

This is a read-only monitor for the isolated Kiro billing test instance and
the matching Sub2API account. It does not change server configuration, restart
containers, issue inference requests, or store credentials, request bodies, or
raw logs.

## Layout

Usage diagnostics now flag local prompt estimates of at least 100,000 tokens
that are at least twice the context estimate, only when provider_usage=false.
This is a review signal about simulated token attribution, not a billing-error
verdict. Missing/invalid fields are not treated as proof of normal metering.
Events use the existing deduplication state. No scheduler is enabled.

- `src/billing_monitor/cli.py`: command-line collection, reconciliation,
  alert de-duplication, and JSON/HTML reports.
- `src/billing_monitor/collector.py`: sanitized SQLite collector sent over SSH
  on standard input.
- `tests/test_billing_monitor.py`: offline standard-library regression tests.
- `config/`: configuration boundaries; no new configuration-loading behavior.

Run from the repository root:

```powershell
python -B -m unittest discover -s monitoring/billing/tests -p test_billing_monitor.py -v
python -B monitoring/billing/src/billing_monitor/cli.py --traces-only --lookback-minutes 30 --output .local-test/billing-monitor/latest.json --html .local-test/billing-monitor/report.html --quiet
```

The old files under `docs/deployment/2026-10-10/` remain thin compatibility
entry points for existing commands. They delegate to this package and contain
no duplicate implementation.

The collector wrapper is a local adapter, not a standalone remote script.
For legacy SSH-stdin callers, run `billing_monitor_collect.py --emit-source`
locally with a JSON parameter object on stdin, then send its stdout to remote
`python3 -`. The current CLI already sends the standalone collector correctly.
Named tests also work through the old test entry point.

## Review hardening (2026-10-12)

Concurrent three-token fallback considers all successful traces, including
already paired ones; removing an exact pair cannot create false uniqueness.
Ambiguous or mature unmatched records in complete windows request manual
review, without claiming confirmed token differences or routing failure.
Routing checks require no account invoices and explicit exclusive scope.

Sub2API pagination uses descending IDs to favor recent records at the cap,
without assuming IDs guarantee timestamp order or stopping on old timestamps.
Page changes, duplicates and caps still mark coverage incomplete. Diagnostic
tail/event limits now mark cache coverage incomplete too.

Explicit image/search fields skip unsupported formula checks; unexplained
differences still alert. Source failures notify again when their sanitized
reason changes. Invalid deduplication state preserves JSON/HTML and returns 1
with an explicit state alert (it cannot deduplicate until the state is fixed).
SSH children exclude configured and standard Sub2API administrator credential
variables while retaining ordinary SSH environment settings.

40 offline regression tests pass, including wrapper discovery and named-test
compatibility. These checks do not validate a live Sub2API version, external
pricing implementation, or actual upstream cache costs. No scheduler is enabled.

Deployment records and existing evidence stay under `docs/deployment/2026-10-10/`.
Runtime reports stay under the ignored `.local-test/billing-monitor/` directory.
See [the operating guide](../../docs/deployment/2026-10-10/BILLING-MONITOR.md)
for authentication, billing rules, exit codes, and verification limits.
