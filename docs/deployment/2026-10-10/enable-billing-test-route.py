"""Run on the deployment host; preserve production configuration privately."""
import pathlib
import subprocess

ROOT = pathlib.Path('/home/deploy/kiro-billing-test-20261010')


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


original = run('docker', 'exec', 'kiro-rs-public', 'cat', '/etc/caddy/Caddyfile')
marker = '    @admin path'
route = '''    @billing_test path /billing-test/v1/models /billing-test/v1/messages /billing-test/v1/messages/count_tokens /billing-test/cc/v1/messages /billing-test/cc/v1/messages/count_tokens
    handle @billing_test {
        uri strip_prefix /billing-test
        reverse_proxy 127.0.0.1:8993 {
            flush_interval -1
        }
    }

'''
if '@billing_test path' in original:
    print('Test route already configured')
    raise SystemExit(0)
if original.count(marker) != 1:
    raise RuntimeError('Unexpected production configuration; no changes made')
files = [('Caddyfile.before-billing-test', original),
         ('Caddyfile.public-test', original.replace(marker, route + marker, 1))]
# Reopening is allowed only when both private snapshots still match live state.
for name, content in files:
    path = ROOT / name
    if path.exists() and path.read_text() != content:
        raise RuntimeError('Saved configuration differs; no changes made')
for name, content in files:
    path = ROOT / name
    if not path.exists():
        path.touch(mode=0o600, exist_ok=False)
        path.write_text(content)
run('docker', 'run', '--rm', '-v', f'{ROOT}/Caddyfile.public-test:/etc/caddy/Caddyfile:ro',
    '-v', '/etc/letsencrypt:/etc/letsencrypt:ro', 'caddy:2',
    'caddy', 'validate', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile')
run('docker', 'run', '--rm', '-v', f'{ROOT}:/test:ro', '-v', '/opt/kiro-rs:/prod',
    'caddy:2', 'sh', '-c', 'cat /test/Caddyfile.public-test > /prod/Caddyfile')
try:
    run('docker', 'exec', 'kiro-rs-public', 'caddy', 'reload', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile')
except subprocess.CalledProcessError:
    run('docker', 'run', '--rm', '-v', f'{ROOT}:/test:ro', '-v', '/opt/kiro-rs:/prod',
        'caddy:2', 'sh', '-c', 'cat /test/Caddyfile.before-billing-test > /prod/Caddyfile')
    run('docker', 'exec', 'kiro-rs-public', 'caddy', 'reload', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile')
    raise RuntimeError('Reload failed; production configuration restored') from None
print('Validated and enabled isolated billing-test route; production routes preserved')
