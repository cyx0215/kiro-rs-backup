"""Read-only structural evidence; never emit bodies, secrets or session IDs."""
import collections
import argparse
import datetime as dt
import json
import pathlib
import re
import sqlite3
import subprocess

root = pathlib.Path('/home/deploy/kiro-billing-test-20261010/data')
parser = argparse.ArgumentParser()
parser.add_argument('--since', default='2026-10-10T16:40:00+08:00')
args = parser.parse_args()
start = dt.datetime.fromisoformat(args.since).timestamp()
with sqlite3.connect(f'file:{root}/traces.db?mode=ro', uri=True) as db:
    db.row_factory = sqlite3.Row
    rows = [dict(r) for r in db.execute(
        'SELECT ts, model, session_id, key_id, final_credential_id, sticky_outcome, '
        'input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens '
        'FROM traces WHERE ts_epoch >= ? ORDER BY ts_epoch', (start,))]
aliases = {}
for row in rows:
    sid = row.pop('session_id')
    row['session_alias'] = aliases.setdefault(sid, len(aliases) + 1) if sid else None

cache_files = list(root.rglob('cache_metering.json'))
cache_stats = []
for path in cache_files:
    entries = json.loads(path.read_text())
    values = list(entries.values())
    cache_stats.append({
        'file': str(path.relative_to(root)), 'entries': len(values),
        'token_counts': sorted(v['tokens'] for v in values),
        'ttl_counts': dict(collections.Counter(v.get('ttl_secs', 300) for v in values)),
    })

logs = subprocess.run(['docker', 'logs', '--since', dt.datetime.fromtimestamp(start, dt.timezone.utc).isoformat(),
                       'kiro-rs-billing-test-20261010'], capture_output=True, text=True)
numeric_context = []
diagnostics = []
allowed_fields = {
    'alias', 'previous_age_secs', 'common_blocks', 'changed_kind',
    'previous_deepest', 'previous_tokens', 'reason', 'blocks',
    'breakpoints', 'lookback_starts', 'prompt_estimate', 'read_estimate',
    'covered_estimate', 'percentage', 'window_size', 'context_tokens',
    'provider_usage', 'fallback_input', 'input', 'output', 'cache_write', 'cache_read',
    'previous_kind', 'current_kind', 'previous_role', 'current_role', 'previous_source',
    'content_equal', 'normalized_equal', 'text_equal', 'previous_bytes', 'current_bytes',
    'changed_fields', 'top_level_auto', 'explicit_breakpoints',
}
for line in (logs.stdout + logs.stderr).splitlines():
    line = re.sub(r'\x1b\[[0-9;]*m', '', line)
    marker = re.search(r'\bbilling_(cache|context|usage)_diagnostic\b', line)
    if marker:
        item = {'kind': marker[1]}
        timestamp = re.match(r'(\d{4}-\d\d-\d\dT[\d:.]+Z)', line)
        if timestamp:
            item['time'] = timestamp[1]
        for field, value in re.findall(
                r'\b([a-z_]+)="?(Some\([\d.]+\)|Some\(true\)|Some\(false\)|None|\[[\d, ]*\]|[a-z_]+|[\d.]+)', line):
            if field not in allowed_fields:
                continue
            if value == 'None':
                parsed = None
            elif value.startswith('Some('):
                inner = value[5:-1]
                parsed = inner == 'true' if inner in ('true', 'false') else float(inner)
            elif value.startswith('['):
                parsed = json.loads(value)
            elif re.fullmatch(r'[\d.]+', value):
                parsed = float(value) if '.' in value else int(value)
            elif value in ('true', 'false'):
                parsed = value == 'true'
            elif field in ('changed_kind', 'reason', 'previous_kind', 'current_kind',
                           'previous_role', 'current_role', 'previous_source'):
                parsed = value
            else:
                continue
            item[field] = parsed
        diagnostics.append(item)
    match = re.search(r'收到 contextUsageEvent: ([\d.]+)%, 计算 input_tokens: (\d+)', line)
    if match:
        numeric_context.append({'percentage': float(match[1]), 'context_tokens': int(match[2])})
print(json.dumps({
    'observed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
    'traces': rows, 'cache_snapshot': cache_stats,
    'context_event_count': len(numeric_context), 'context_events': numeric_context[-20:],
    'diagnostics': diagnostics,
    'log_read_exit': logs.returncode,
}, ensure_ascii=False, indent=2))
