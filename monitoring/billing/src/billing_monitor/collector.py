"""Executed over SSH on the test server; emits only allowlisted telemetry."""

import collections
import contextlib
import datetime as dt
import hashlib
import json
import re
import sqlite3
import subprocess
import urllib.parse


since = dt.datetime.fromisoformat(PARAMS["since"])
until = dt.datetime.fromisoformat(PARAMS["until"])
db_uri = "file:" + urllib.parse.quote(PARAMS["db_path"], safe="/") + "?mode=ro"
with contextlib.closing(sqlite3.connect(db_uri, uri=True)) as db:
    db.row_factory = sqlite3.Row
    db.execute("BEGIN")
    raw = [dict(row) for row in db.execute(
        "SELECT trace_id, ts, model, final_status, duration_ms, error_type, "
        "input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens, "
        "credits, usage_source, session_id, "
        "CASE WHEN error_message LIKE '%所有凭据均已禁用%' "
        "OR error_message LIKE '%所有凭据均无法获取有效 Token%' "
        "OR error_message LIKE '%No available credentials%' THEN 1 ELSE 0 END "
        "AS no_schedulable_credentials FROM traces "
        "WHERE ts_epoch + duration_ms / 1000.0 >= ? AND ts_epoch <= ? "
        "AND ts_epoch + duration_ms / 1000.0 <= ? "
        "ORDER BY ts_epoch DESC, trace_id DESC LIMIT ?",
        (since.timestamp() - 1, int(until.timestamp()), until.timestamp() + 1,
         PARAMS["max_rows"] + 1),
    )]
    truncated = len(raw) > PARAMS["max_rows"]
    raw = raw[:PARAMS["max_rows"]]
    attempt_statuses = collections.defaultdict(set)
    for offset in range(0, len(raw), 500):
        ids = [row["trace_id"] for row in raw[offset:offset + 500]]
        placeholders = ",".join("?" for _ in ids)
        for trace_id, status in db.execute(
            "SELECT trace_id, http_status FROM trace_attempts "
            f"WHERE trace_id IN ({placeholders}) AND http_status IS NOT NULL", ids,
        ):
            attempt_statuses[trace_id].add(status)

session_aliases = {}
traces = []
for row in raw:
    trace_id = row.pop("trace_id")
    session_id = row.pop("session_id")
    row["ref"] = hashlib.sha256(trace_id.encode()).hexdigest()[:16]
    row["session_alias"] = (
        session_aliases.setdefault(session_id, len(session_aliases) + 1)
        if session_id else None
    )
    row["attempt_http_statuses"] = sorted(attempt_statuses[trace_id])
    if row["error_type"] and not re.fullmatch(r"[A-Za-z0-9_.-]{1,60}", row["error_type"]):
        row["error_type"] = "other"
    traces.append(row)

def docker_read(command, timeout):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(command, 1, "", "")


inspect = docker_read(
    ["docker", "inspect", "--format", "{{.Image}}|{{.State.Status}}|{{.RestartCount}}",
     PARAMS["container"]], 15,
)
container = None
if inspect.returncode == 0:
    fields = inspect.stdout.strip().split("|")
    if len(fields) == 3:
        container = {"image": fields[0], "status": fields[1], "restarts": int(fields[2])}

logs = docker_read(
    ["docker", "logs", "--timestamps", "--since", since.isoformat(),
     "--until", until.isoformat(), "--tail", "5000", PARAMS["container"]],
    30,
)
diagnostics = []
reason_counts = collections.Counter()
log_lines = logs.stdout.splitlines() + logs.stderr.splitlines()
if logs.returncode == 0:
    for line in log_lines:
        line = re.sub(r'\x1b\[[0-9;]*m', '', line)
        marker = re.search(r"\bbilling_(cache|context|usage)_diagnostic\b", line)
        if not marker:
            continue
        reason = re.search(r'\breason="?([a-z_]{1,60})', line)
        if marker.group(1) == "cache" and not reason:
            continue
        if reason:
            reason_counts[reason.group(1)] += 1
        item = {"kind": marker.group(1)}
        if reason:
            item["reason"] = reason.group(1)
        stamp = re.match(r"(\d{4}-\d\d-\d\dT[\d:.]+Z)", line)
        if stamp:
            item["time"] = stamp.group(1)
        for name in ("alias", "previous_age_secs", "common_blocks", "previous_deepest",
                     "previous_tokens", "blocks", "prompt_estimate", "read_estimate",
                     "covered_estimate", "context_tokens", "cache_write"):
            match = re.search(r"\b" + name + r"=(\d+)", line)
            if match:
                item[name] = int(match.group(1))
            else:
                match = re.search(r"\b" + name + r"=Some\((\d+)\)", line)
                if match:
                    item[name] = int(match.group(1))
        for name in ("previous_kind", "current_kind", "previous_role", "current_role",
                     "previous_source"):
            match = re.search(r"\b" + name + r'="?([a-z_]{1,60})', line)
            if match:
                item[name] = match.group(1)
        for name in ("normalized_equal", "text_equal", "provider_usage"):
            match = re.search(r"\b" + name + r"=(?:Some\()?((?:true|false))", line)
            if match:
                item[name] = match.group(1) == "true"
        diagnostics.append(item)

print(json.dumps({
    "observed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    "traces": traces,
    "traces_truncated": truncated,
    "container": container,
    "diagnostics": diagnostics[-50:],
    "diagnostic_reason_counts_in_log_tail": dict(reason_counts),
    "diagnostics_available": logs.returncode == 0,
    "diagnostics_truncated": len(log_lines) >= 5000 or len(diagnostics) > 50,
    "diagnostic_log_tail_limit": 5000,
}, separators=(",", ":")))
