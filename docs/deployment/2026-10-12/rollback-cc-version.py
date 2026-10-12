"""Rollback only the 8993 image. Requires explicit user authorization for --execute.

Reads existing traces/logs to check for unfinished requests. Does not run inference.
Credentials and raw logs stay on the server. Backups have restricted permissions.
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path('/home/deploy/kiro-billing-test-20261010')
NAME = 'kiro-rs-billing-test-20261010'
PROD = 'kiro-rs-zyphrzero-test'
CURRENT = 'sha256:e54e5be78a04abbb4fe7fda1748d75d0d22a6c981b1c1d8be9266665b1c8ffd2'
TARGET = 'sha256:ba74e53a06cf097c587f658ae6222047b404f9045f1c9e08f67c55bdbfdc359a'
BASE = ROOT / 'compose.billing-test.yaml'
OVERLAY = ROOT / 'rollback-cc-20261012T050316Z/compose.rollback.yaml'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def run(*args):
    result = subprocess.run(args, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('Command failed: ' + args[0])
    return result.stdout.strip()


def inspect(name):
    return json.loads(run('docker', 'inspect', name))[0]


def stamp(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def request_state():
    now = dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(minutes=20)
    result = subprocess.run(['docker', 'logs', '--since', since.isoformat(), NAME],
                            text=True, capture_output=True, check=True)
    starts = []
    for raw in (result.stdout + '\n' + result.stderr).splitlines():
        line = re.sub(r'\x1b\[[0-9;]*m', '', raw)
        if 'Received POST' not in line or 'messages request' not in line:
            continue
        ts = re.search(r'\d{4}-\d{2}-\d{2}T[0-9:.]+Z', line)
        model = re.search(r'\bmodel=([\w.\-]+)', line)
        if ts and model:
            starts.append((stamp(ts.group()), model.group(1)))
    db = sqlite3.connect('file:' + str(ROOT / 'data/traces.db') + '?mode=ro', uri=True)
    traces = list(db.execute('SELECT ts, model FROM traces WHERE ts >= ? ORDER BY ts',
                            (since.isoformat(),)))
    db.close()
    used = set()
    unmatched = 0
    for started, model in sorted(starts):
        candidates = [(stamp(row[0]) - started, i) for i, row in enumerate(traces)
                      if i not in used and row[1] == model and 0 <= stamp(row[0]) - started <= 20]
        if candidates:
            used.add(min(candidates)[1])
        else:
            unmatched += 1
    quiet = now.timestamp() - max((x[0] for x in starts), default=since.timestamp())
    tcp = run('docker', 'exec', NAME, 'cat', '/proc/net/tcp', '/proc/net/tcp6')
    outgoing = sum(1 for line in tcp.splitlines() if len(line.split()) >= 4
                   and line.split()[3] == '01' and not line.split()[1].endswith(':231E'))
    return {'observed_requests': len(starts), 'unmatched_requests': unmatched,
            'quiet_seconds': round(quiet, 1), 'established_outgoing_connections': outgoing,
            'match_basis': 'model and start time within 20 seconds; no shared request ID'}


def http(path, key=None):
    headers = {'x-api-key': key} if key else {}
    req = urllib.request.Request('http://127.0.0.1:8993' + path, headers=headers)
    try:
        with OPENER.open(req, timeout=3) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, b''


def ready():
    for _ in range(20):
        try:
            if http('/admin')[0] == 200:
                return True
        except OSError:
            pass
        time.sleep(1)
    return False


def account_settings(rows):
    return sorted([{'id': row['id'], 'priority': row.get('priority', 0),
                    'disabled': row.get('disabled', False)} for row in rows], key=lambda row: row['id'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    before = inspect(NAME)
    production = inspect(PROD)
    assert before['Image'] == CURRENT and before['State']['Running'], 'Unexpected running image'
    assert json.loads(run('docker', 'image', 'inspect', TARGET))[0]['Id'] == TARGET
    effective = json.loads(run('docker', 'compose', '-f', str(BASE), '-f', str(OVERLAY),
                               'config', '--format', 'json'))['services']['billing-test']
    assert effective['image'] == TARGET and effective['container_name'] == NAME
    assert any(x.get('source') == str(ROOT / 'data') and x.get('target') == '/app/config'
               for x in effective['volumes'])
    assert any(str(x.get('published')) == '8993' and x.get('host_ip') == '127.0.0.1'
               for x in effective['ports'])
    state = request_state()
    if not args.execute:
        print(json.dumps({'dry_run': True, 'target_image': TARGET, 'request_state': state}))
        return
    assert state['unmatched_requests'] == 0 and state['quiet_seconds'] >= 30, 'Requests not quiet'
    if state['established_outgoing_connections']:
        assert state['quiet_seconds'] >= 90, 'Waiting for possible idle connection to settle'
    os.umask(0o077)
    backup = ROOT / ('before-version-rollback-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    restore = backup / 'compose.restore-current.yaml'
    restore.write_text('services:\n  billing-test:\n    image: ' + CURRENT + '\n    environment:\n      KIRO_BILLING_DIAGNOSTICS: "1"\n')
    run('docker', 'stop', '--time', '30', NAME)
    try:
        shutil.copytree(ROOT / 'data', backup / 'data')
        config_before = (ROOT / 'data/config.json').read_bytes()
        credentials_before = json.loads((ROOT / 'data/credentials.json').read_text())
        run('docker', 'compose', '-f', str(BASE), '-f', str(OVERLAY), 'up', '-d', '--no-deps', 'billing-test')
        assert ready(), 'Admin did not become healthy'
        after = inspect(NAME)
        assert after['Image'] == TARGET and after['State']['Running']
        assert http('/v1/models')[0] == 401, 'API authentication changed'
        config = json.loads((ROOT / 'data/config.json').read_text())
        assert (ROOT / 'data/config.json').read_bytes() == config_before, 'Config changed'
        credentials = json.loads((ROOT / 'data/credentials.json').read_text())
        assert account_settings(credentials) == account_settings(credentials_before), 'Account settings changed'
        assert http('/v1/models', config['apiKey'])[0] == 200, 'Authenticated model listing failed'
        runtime_status, runtime_body = http('/api/admin/credentials', config['adminApiKey'])
        assert runtime_status == 200, 'Admin credentials query failed'
        runtime = json.loads(runtime_body)
        runtime_rows = runtime['credentials']
        assert account_settings(runtime_rows) == account_settings(credentials), 'Runtime settings differ'
        prod_after = inspect(PROD)
        assert prod_after['Id'] == production['Id'] and prod_after['State']['StartedAt'] == production['State']['StartedAt']
    except Exception as error:
        run('docker', 'compose', '-f', str(BASE), '-f', str(restore), 'up', '-d', '--no-deps', 'billing-test')
        print(json.dumps({'rollback_failed': True, 'error_class': type(error).__name__,
                          'restored_previous_image': inspect(NAME)['Image'] == CURRENT,
                          'healthy': ready(), 'backup': str(backup)}), flush=True)
        raise SystemExit(1)
    report = {'completed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'previous_image': CURRENT, 'image': TARGET, 'started_at': after['State']['StartedAt'],
              'backup': str(backup), 'active_compose_overlay': str(OVERLAY),
              'production_unchanged': True, 'admin_status': 200, 'unauth_models_status': 401,
              'authenticated_models_status': 200, 'runtime_credentials_status': 200,
              'config_unchanged': True, 'mode': config.get('loadBalancingMode'),
              'credentials': account_settings(credentials), 'request_state_before': state,
              'inference_tested': False}
    (backup / 'result.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
