"""Read-only, sanitized usage snapshot; no credentials or request bodies."""
import datetime as dt
import json
import sqlite3

start = dt.datetime.fromisoformat("2026-10-10T16:40:00+08:00").timestamp()
with sqlite3.connect(
    "file:/home/deploy/kiro-billing-test-20261010/data/traces.db?mode=ro", uri=True
) as db:
    db.row_factory = sqlite3.Row
    rows = [dict(row) for row in db.execute(
        "SELECT ts, ts_epoch, model, is_stream, final_status, input_tokens, "
        "output_tokens, cache_creation_tokens, cache_read_tokens, credits, usage_source "
        "FROM traces WHERE ts_epoch >= ? ORDER BY ts_epoch DESC", (start,)
    )]
    statuses = {}
    for row in rows:
        statuses[row["final_status"]] = statuses.get(row["final_status"], 0) + 1
    print(json.dumps({
        "observed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "since": "2026-10-10T16:40:00+08:00",
        "scope": "test server traces; not necessarily one Sub2API account",
        "count": len(rows), "statuses": statuses,
        "credits": sum(row["credits"] or 0 for row in rows),
        "latest_ts_epoch": max((row["ts_epoch"] for row in rows), default=None),
        "latest": rows[:20],
    }, ensure_ascii=False, indent=2))
