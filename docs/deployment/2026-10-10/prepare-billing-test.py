import json
import os
import secrets
from pathlib import Path

os.umask(0o077)
source = Path('/home/deploy/kiro-zyphrzero-test/data')
target = Path('/home/deploy/kiro-billing-test-20261010/data')
target.mkdir(parents=True, exist_ok=False)
config = json.loads((source / 'config.json').read_text())
config.update({
    'host': '0.0.0.0', 'port': 8990,
    'apiKey': secrets.token_hex(32),
    'sub2apiUsageCompat': True,
    'updateAutoApply': False,
    'traceEnabled': True,
})
(target / 'config.json').write_text(json.dumps(config, indent=2))
credentials = json.loads((source / 'credentials.json').read_text())
(target / 'credentials.json').write_text(json.dumps(credentials, indent=2))
print(json.dumps({'prepared': True, 'port': 8993, 'sub2apiUsageCompat': True,
                  'isolated_data': True, 'independent_api_key': True}))
