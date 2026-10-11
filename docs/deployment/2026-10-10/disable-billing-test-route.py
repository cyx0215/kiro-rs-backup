"""Restore the private backup only when the live config still matches our test config."""
from pathlib import Path
import subprocess

ROOT = Path('/home/deploy/kiro-billing-test-20261010')


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


current = run('docker', 'exec', 'kiro-rs-public', 'cat', '/etc/caddy/Caddyfile')
original = (ROOT / 'Caddyfile.before-billing-test').read_text()
if current == original:
    print('Test route already removed')
    raise SystemExit(0)
assert current == (ROOT / 'Caddyfile.public-test').read_text(), 'Live config changed; automatic restore refused'
run('docker', 'run', '--rm', '-v', f'{ROOT}/Caddyfile.before-billing-test:/etc/caddy/Caddyfile:ro',
    '-v', '/etc/letsencrypt:/etc/letsencrypt:ro', 'caddy:2',
    'caddy', 'validate', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile')
run('docker', 'run', '--rm', '-v', f'{ROOT}:/test:ro', '-v', '/opt/kiro-rs:/prod',
    'caddy:2', 'sh', '-c', 'cat /test/Caddyfile.before-billing-test > /prod/Caddyfile')
try:
    run('docker', 'exec', 'kiro-rs-public', 'caddy', 'reload', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile')
except subprocess.CalledProcessError:
    run('docker', 'run', '--rm', '-v', f'{ROOT}:/test:ro', '-v', '/opt/kiro-rs:/prod',
        'caddy:2', 'sh', '-c', 'cat /test/Caddyfile.public-test > /prod/Caddyfile')
    run('docker', 'exec', 'kiro-rs-public', 'caddy', 'reload', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile')
    raise RuntimeError('Restore failed; test configuration reloaded') from None
print('Test route removed; original production configuration restored')
