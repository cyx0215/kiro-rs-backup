# Configuration boundary

The monitor continues to use its existing CLI flags and environment variables.
No automatic configuration-file loading is introduced by this directory move.

Only non-sensitive examples belong here. Keep credentials outside the repository,
and pass optional sanitized historical prices explicitly with `--prices-json`.
Deployment-specific settings and historical evidence remain under
`docs/deployment/2026-10-10/`. Runtime reports remain in `.local-test/billing-monitor/`.
