"""Synchronize reviewed 8993 accounts to 8992 via Admin API; secrets stay on host."""
import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import urllib.request

ROOTS = {8992: pathlib.Path('/home/deploy/kiro-zyphrzero-test/data'),
         8993: pathlib.Path('/home/deploy/kiro-billing-test-20261010/data')}
NAMES = {8992: 'kiro-rs-zyphrzero-test', 8993: 'kiro-rs-billing-test-20261010'}
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
AUTH_FIELDS = ('accessToken', 'refreshToken', 'expiresAt', 'clientId', 'clientSecret', 'kiroApiKey')
IMPORT_FIELDS = ('refreshToken', 'accessToken', 'profileArn', 'expiresAt', 'authMethod',
                 'provider', 'clientId', 'clientSecret', 'startUrl', 'tokenEndpoint',
                 'issuerUrl', 'scopes', 'priority', 'region', 'authRegion', 'apiRegion',
                 'machineId', 'email', 'proxyUrl', 'proxyUsername', 'proxyPassword',
                 'kiroApiKey', 'endpoint', 'groups', 'sourceChannel', 'metadata')


def read(port, name):
    return json.loads((ROOTS[port] / name).read_text())


def api(port, path, payload=None):
    req = urllib.request.Request(f'http://127.0.0.1:{port}/api/admin' + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'x-api-key': read(port, 'config.json')['adminApiKey'],
                 'Content-Type': 'application/json'})
    with OPENER.open(req, timeout=90) as response:
        return json.load(response)


def inspect(port):
    row = json.loads(subprocess.check_output(['docker', 'inspect', NAMES[port]]))[0]
    return {'id': row['Id'], 'image': row['Image'], 'started_at': row['State']['StartedAt']}


def settings(row):
    return {'id': row['id'], 'disabled': row.get('disabled', False), 'priority': row.get('priority', 0)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    source = {x['id']: x for x in read(8993, 'credentials.json')}
    target = {x['id']: x for x in read(8992, 'credentials.json')}
    assert set(source) == {2, 3, 4, 5, 6} and set(target) == {2, 3, 4, 5}, 'Account inventory changed'
    assert {i for i, x in source.items() if not x.get('disabled')} == {5, 6}, 'Source state changed'
    for i in target:
        assert source[i].get('refreshToken') and source[i]['refreshToken'] == target[i].get('refreshToken'), 'Account identity changed'
        for key in IMPORT_FIELDS:
            if key not in ('accessToken', 'expiresAt', 'priority'):
                assert source[i].get(key) == target[i].get(key), 'Unreviewed credential differences'
    containers = {p: inspect(p) for p in ROOTS}
    assert containers[8992]['image'] == 'sha256:8009e1a137d85c4d1404922acb589075265a076cbe52947f7128c847c7e95895'
    plan = {'source_port': 8993, 'destination_port': 8992, 'import_source_ids': [6],
            'destination_settings': [settings(source[i]) for i in sorted(source)],
            'existing_tokens_preserved': True, 'restart_required': False}
    if not args.execute:
        print(json.dumps(plan, indent=2))
        return
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = ROOTS[8992].parent / ('credentials-before-sync-' + stamp)
    backup.mkdir(mode=0o700)
    for port in ROOTS:
        (backup / f'credentials-{port}.json').write_bytes((ROOTS[port] / 'credentials.json').read_bytes())
        (backup / f'runtime-{port}.json').write_text(json.dumps(api(port, '/credentials')))
    config_before = {p: (ROOTS[p] / 'config.json').read_bytes() for p in ROOTS}
    actions = []
    try:
        payload = {k: source[6][k] for k in IMPORT_FIELDS if k in source[6]}
        result = api(8992, '/credentials', payload)
        assert result.get('success'), 'Credential import rejected'
        new_id = result['credentialId']
        actions.append({'action': 'import', 'source_id': 6, 'destination_id': new_id})
        for i in sorted(target):
            if source[i].get('priority', 0) != target[i].get('priority', 0):
                api(8992, f'/credentials/{i}/priority', {'priority': source[i].get('priority', 0)})
                actions.append({'action': 'priority', 'id': i, 'priority': source[i].get('priority', 0)})
            if source[i].get('disabled', False) != target[i].get('disabled', False):
                api(8992, f'/credentials/{i}/disabled', {'disabled': source[i].get('disabled', False)})
                actions.append({'action': 'disabled', 'id': i, 'disabled': source[i].get('disabled', False)})
        runtime = api(8992, '/credentials')
        after = {x['id']: x for x in read(8992, 'credentials.json')}
        live = {x['id']: x for x in runtime['credentials']}
        mapping = {i: i for i in target}
        mapping[6] = new_id
        assert set(after) == set(live) == set(mapping.values()), 'Account count mismatch'
        for src_id, dst_id in mapping.items():
            for k, default in (('disabled', False), ('priority', 0)):
                assert source[src_id].get(k, default) == after[dst_id].get(k, default) == live[dst_id].get(k, default), 'Settings verification failed'
        assert after[new_id].get('email') == source[6].get('email'), 'Imported identity mismatch'
        assert all(inspect(p) == containers[p] for p in ROOTS), 'Container changed'
        assert all((ROOTS[p] / 'config.json').read_bytes() == config_before[p] for p in ROOTS), 'Service config changed'
        report = dict(plan, completed_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                      backup=str(backup), actions=actions, account_id_mapping=mapping,
                      total=runtime['total'], enabled=runtime['available'],
                      runtime_and_disk_verified=True, containers_unchanged=True,
                      config_unchanged=True, inference_sent=False,
                      existing_auth_fields_unchanged=all(after[i].get(k) == target[i].get(k) for i in target for k in AUTH_FIELDS),
                      accounts=[{k: x.get(k) for k in ('id','disabled','priority','disabledReason','subscriptionTitle')} for x in runtime['credentials']])
        (backup / 'result.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
    except Exception as error:
        report = {'failed': True, 'error_class': type(error).__name__, 'backup': str(backup), 'completed_actions': actions}
        (backup / 'failure.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
