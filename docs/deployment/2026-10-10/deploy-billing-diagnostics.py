"""Deploy the test-only diagnostic overlay after checking idle connections."""
import datetime as dt
import argparse
import hashlib
import http.client
import json
import pathlib
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request

root = pathlib.Path('/home/deploy/kiro-billing-test-20261010')
container = 'kiro-rs-billing-test-20261010'
parser = argparse.ArgumentParser()
parser.add_argument('--expected-image', default='sha256:5ad4e546f051e5ee734ed24e3b5cb0c163da1be0646a06d5ef5902147f66be8e')
parser.add_argument('--image', default='kiro-rs:billing-diag-20261010')
parser.add_argument('--archive', default='billing-diag-image')
parser.add_argument('--overlay', default='compose.billing-diagnostics.yaml')
parser.add_argument('--rollback-overlay')
args = parser.parse_args()

def run(*args):
    return subprocess.check_output(args, text=True).strip()

old_image = run('docker', 'inspect', container, '--format', '{{.Image}}')
if old_image != args.expected_image:
    raise SystemExit('Test image changed; inspect before deploying')

# Inbound keep-alive connections include the polling admin page.
# Check upstream connections and a quiet interval in completed traces as well.
connections = run('docker', 'exec', container, 'cat', '/proc/net/tcp', '/proc/net/tcp6')
active = sum(1 for line in connections.splitlines()
             if len(line.split()) >= 4 and line.split()[1].endswith(':231E')
             and line.split()[3] == '01')
upstream = sum(1 for line in connections.splitlines()
               if len(line.split()) >= 4 and not line.split()[1].endswith(':231E')
               and line.split()[3] == '01')
with sqlite3.connect(f'file:{root}/data/traces.db?mode=ro', uri=True) as db:
    latest = db.execute('SELECT MAX(ts_epoch) FROM traces').fetchone()[0]
if upstream or (latest is not None and time.time() - latest < 60):
    raise SystemExit(f'Test service is not quiet: {upstream} upstream connections; retry later')

archive = root / f'{args.archive}.tar'
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
expected_digest = (root / f'{args.archive}.sha256').read_text().strip()
if digest != expected_digest:
    raise SystemExit('Image archive checksum mismatch')
run('docker', 'load', '-i', str(archive))
new_image = run('docker', 'image', 'inspect', args.image,
                '--format', '{{.Id}}')
subprocess.run(['docker', 'compose', '-f', str(root / 'compose.billing-test.yaml'),
                '-f', str(root / args.overlay),
                'up', '-d', '--no-deps', 'billing-test'], check=True)
health = None
for _ in range(20):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8993/admin', timeout=2) as response:
            health = response.status
        if health == 200:
            break
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        pass
    time.sleep(0.5)
if health != 200:
    rollback = ['docker', 'compose', '-f', str(root / 'compose.billing-test.yaml')]
    if args.rollback_overlay:
        rollback += ['-f', str(root / args.rollback_overlay)]
    subprocess.run(rollback + ['up', '-d', '--no-deps', 'billing-test'], check=True)
    raise SystemExit('Diagnostic service health check failed; restored previous test configuration')
print(json.dumps({
    'deployed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
    'container': container, 'previous_image': old_image, 'image': new_image,
    'archive_sha256': digest, 'established_connections_before': active,
    'upstream_connections_before': upstream,
    'status': run('docker', 'inspect', container, '--format', '{{.State.Status}}'),
    'admin_http_status': health,
    'diagnostics_enabled': 'KIRO_BILLING_DIAGNOSTICS=1' in json.loads(
        run('docker', 'inspect', container))[0]['Config']['Env'],
    'production_image': run('docker', 'inspect', 'kiro-rs-zyphrzero-test',
                            '--format', '{{.Image}}'),
}, indent=2))
