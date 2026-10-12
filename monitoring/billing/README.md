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
Events use the existing deduplication state. The local resident runner is
available separately; it does not enable a Codex scheduled task.

- `src/billing_monitor/cli.py`: command-line collection, reconciliation,
  alert de-duplication, and JSON/HTML reports.
- `src/billing_monitor/collector.py`: sanitized SQLite collector sent over SSH
  on standard input.
- `src/billing_monitor/runner.py`: sequential resident collection and an
  allowlisted HTTP dashboard bound only to localhost.
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

## Resident local monitor

Start from the repository root (foreground command; the deployed instance was
started as a hidden Windows background process):

```powershell
python -B monitoring/billing/src/billing_monitor/runner.py --mode traces-only --interval-seconds 900 --port 8994
```

Open `http://127.0.0.1:8994/report.html`. Collection starts immediately and then
waits 15 minutes after each completed cycle. New runner instances serve a dynamic
dashboard that reads sanitized snapshots every five seconds without reloading.
`/status.json` contains PID, mode, last run time, result and report
freshness. Only report.html, latest.json and status.json are served; no directory
listing or historical/raw data is exposed. A failure does not terminate the
loop, and repeated alert suppression uses the CLI's source-specific state.

This mode has no Sub2API invoice collection. For full mode, inject an existing
admin credential into the launch environment and select `--mode full` with
`--token-env` and `--auth-type`. No credential is accepted as a command argument.
Changing the parent shell environment does not update a running process.
The runner retains sanitized snapshots only and does not send external messages.

The background process runs until it is stopped or Windows shuts down; no
boot startup or service installation is configured. To stop it, verify the
PID in `/status.json` belongs to this runner, then stop that process. A PID file
under `.local-test/billing-monitor/runner.pid` records the launched instance.
Run all 43 offline/local regression tests with:

```powershell
python -B -m unittest discover -s monitoring/billing/tests -p 'test_*.py'
```

## Dynamic dashboard without restarting collection

The current full collector on port 8994 retains its credential in memory.
To upgrade the display without interrupting that process, start a separate
read-only dashboard on port 8995:

```powershell
python -B monitoring/billing/src/billing_monitor/runner.py --dashboard-only --port 8995 --output-dir .local-test/billing-monitor
```

Open `http://127.0.0.1:8995/report.html`. This process never collects, requires
no administrator credential, and never writes the collector status. It serves
only the dashboard and existing latest/status JSON snapshots on loopback.
The live dashboard is separate from the static HTML export, so the old
collector can keep overwriting its report without reverting the new UI.

Features: automatic five-second reads, pause/resume, manual display refresh,
data age and stale/error indicators, grouped alerts, model/ID search, and
pairing filters. Display refresh does not trigger collection or inference.
The current collector still waits 15 minutes between cycles. A fresh HTTP
connection does not mean fresh collected data. Reports older than 20 minutes
or explicitly marked stale/unavailable show a warning. Filters survive updates.

The dashboard is a local background process, not a Windows service; it does
not start at boot. Its current PID is saved separately in
`.local-test/billing-monitor/dashboard.pid`. Verify process identity before
stopping it. The existing collector PID remains in `runner.pid`.
The expanded suite has 45 offline/local regression tests.
