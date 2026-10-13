"""Deploy only 8992, pin images and preserve data. Default is read-only preflight."""
import argparse
import datetime as dt
import hashlib
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

ROOT = pathlib.Path('/home/deploy/kiro-zyphrzero-test')
NAME = 'kiro-rs-zyphrzero-test'
PROD = 'kiro-rs-billing-test-20261010'
EXPECTED = 'sha256:fd6f06cc18427faf6aa4b1088c2ce443088efca3ca67d2f23644bf48cd8a0256'
REVISION = '62ae534'
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
    req = urllib.request.Request('http://127.0.0.1:8992' + path, headers=headers)
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
    parser.add_argument('--archive')
    parser.add_argument('--sha256')
    parser.add_argument('--image')
    args = parser.parse_args()
    os.umask(0o077)
    before, production = inspect(NAME), inspect(PROD)
    assert before['Image'] == EXPECTED and before['State']['Running'], 'Unexpected production baseline'
    labels = before['Config']['Labels']
    compose_files = labels['com.docker.compose.project.config_files'].split(',')
    compose = ['docker', 'compose', '-p', labels['com.docker.compose.project']]
    for file in compose_files:
        compose += ['-f', file]
    effective = json.loads(run(*compose, 'config', '--format', 'json'))
    service = effective['services']['kiro-test']
    assert service['container_name'] == NAME
    assert len(service['ports']) == 1
    assert any(str(p.get('published')) == '8992' and p.get('host_ip') == '127.0.0.1'
               and str(p.get('target')) == '8990' for p in service['ports'])
    assert len(service['volumes']) == 1
    assert any(v.get('source') == str(ROOT / 'data') and v.get('target') == '/app/config'
               for v in service['volumes'])
    assert before['HostConfig']['PortBindings']['8990/tcp'] == [{'HostIp':'127.0.0.1','HostPort':'8992'}]
    for key, value in service.get('environment', {}).items():
        assert f'{key}={value}' in before['Config']['Env'], 'Compose environment drift'
    current_image = json.loads(run('docker', 'image', 'inspect', service['image']))[0]['Id']
    assert current_image == EXPECTED, 'Compose image drift'
    state = request_state()
    if not args.execute:
        print(json.dumps({'dry_run': True, 'current_image': EXPECTED, 'request_state': state,
                          'project': labels['com.docker.compose.project'], 'compose_files': compose_files}))
        return
    assert args.archive and args.sha256 and args.image, 'Missing release identity'
    archive = pathlib.Path(args.archive).resolve()
    assert archive.parent == ROOT, 'Archive must be in production deployment directory'
    hasher = hashlib.sha256()
    with archive.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    assert digest == args.sha256, 'Archive checksum mismatch'
    run('docker', 'load', '-i', str(archive))
    target = json.loads(run('docker', 'image', 'inspect', args.image))[0]['Id']
    assert target != EXPECTED
    # Recheck immediately before stopping; correlation is heuristic, not a shared request ID.
    state = request_state()
    assert state['unmatched_requests'] == 0 and state['quiet_seconds'] >= 30, 'Requests not quiet'
    if state['established_outgoing_connections']:
        assert state['quiet_seconds'] >= 90, 'Waiting for possible idle connection to settle'
    assert inspect(NAME)['Id'] == before['Id'], 'Production container changed during preparation'
    stamp_id = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = ROOT / ('rollback-upstream-' + stamp_id)
    backup.mkdir(mode=0o700)
    for i, path in enumerate(compose_files):
        shutil.copy2(path, backup / ('prior-compose-' + str(i) + '.yaml'))
    rollback_service = dict(service, image=EXPECTED, pull_policy='never')
    release_service = dict(service, image=target, pull_policy='never')
    rollback = backup / 'compose.rollback.json'
    release = ROOT / ('compose.upstream-' + REVISION + '-' + stamp_id + '.json')
    common = {k:v for k,v in effective.items() if k != 'services'}
    rollback.write_text(json.dumps(dict(common, services={'kiro-test':rollback_service}), indent=2))
    release.write_text(json.dumps(dict(common, services={'kiro-test':release_service}), indent=2))
    def up(path):
        return run('docker', 'compose', '-p', labels['com.docker.compose.project'], '-f', str(path),
                   'up', '-d', '--no-deps', 'kiro-test')
    try:
        run('docker', 'stop', '--time', '30', NAME)
        shutil.copytree(ROOT / 'data', backup / 'data')
        config_before = (ROOT / 'data/config.json').read_bytes()
        creds_before = json.loads((ROOT / 'data/credentials.json').read_text())
        up(release)
        assert ready(), 'Admin did not become healthy'
        after = inspect(NAME)
        assert after['Image'] == target and after['State']['Running']
        assert after['HostConfig']['PortBindings'] == before['HostConfig']['PortBindings']
        assert sorted(after['Config']['Env']) == sorted(before['Config']['Env']), 'Environment changed'
        assert http('/v1/models')[0] == 401, 'API authentication changed'
        config = json.loads((ROOT / 'data/config.json').read_text())
        assert (ROOT / 'data/config.json').read_bytes() == config_before, 'Config changed'
        creds = json.loads((ROOT / 'data/credentials.json').read_text())
        assert account_settings(creds) == account_settings(creds_before), 'Account settings changed'
        assert http('/v1/models', config['apiKey'])[0] == 200, 'Authenticated model listing failed'
        status, body = http('/api/admin/credentials', config['adminApiKey'])
        assert status == 200, 'Admin credentials query failed'
        assert account_settings(json.loads(body)['credentials']) == account_settings(creds)
        prod_after = inspect(PROD)
        assert prod_after['Id'] == production['Id'] and prod_after['Image'] == production['Image']
        assert prod_after['State']['StartedAt'] == production['State']['StartedAt']
    except Exception as error:
        rollback_ok = False
        try:
            up(rollback)
            rollback_ok = ready() and inspect(NAME)['Image'] == EXPECTED
        except Exception:
            pass
        failure = {'deployment_failed':True, 'error_class':type(error).__name__,
                   'restored_previous_image_and_healthy':rollback_ok, 'backup':str(backup)}
        (backup / 'failure.json').write_text(json.dumps(failure, indent=2))
        print(json.dumps(failure), flush=True)
        raise SystemExit(1)
    report = {'completed_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
              'source_commit':REVISION, 'image':target, 'previous_image':EXPECTED,
              'archive_sha256':digest, 'started_at':after['State']['StartedAt'],
              'backup':str(backup), 'active_compose':str(release), 'rollback_compose':str(rollback),
              'compose_project':labels['com.docker.compose.project'],
              'test_8993_unchanged':True, 'admin_status':200, 'unauth_models_status':401,
              'authenticated_models_status':200, 'runtime_credentials_status':200,
              'config_unchanged':True, 'environment_unchanged':True,
              'local_billing_compatibility_removed':True,
              'load_balancing_mode':config.get('loadBalancingMode'),
              'credentials':account_settings(creds), 'request_state_before':state,
              'inference_tested':False}
    (backup / 'result.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
