"""Two real downstream requests; save usage only, never response content or keys."""
import json
import os
import time
import urllib.request
import urllib.error
import uuid
import argparse
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--smoke', action='store_true', help='One small downstream request')
options = parser.parse_args()
key_path = Path(os.environ.get('BILLING_TEST_KEY_FILE', str(Path(os.environ.get('TEMP', '/tmp')) / 'kiro-sub2api-test-key.txt')))
key = key_path.read_text().strip()
key_path.unlink()
assert key.startswith('sk-') and len(key) > 20
payload = {
    'model': 'claude-sonnet-4-20250514', 'stream': True, 'max_tokens': 32,
    'metadata': {'user_id': json.dumps({'session_id': str(uuid.uuid4())})},
    'messages': [{'role': 'user', 'content': [{
        'type': 'text',
        'text': ('This is a repeated billing validation paragraph. ' * 180) + '\nReply only OK.',
        'cache_control': {'type': 'ephemeral'},
    }]}],
}
result = {'tested_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
          'endpoint': 'https://02acc.itssx.com/v1/messages', 'model': payload['model'],
          'requests': []}
if options.smoke:
    payload['messages'] = [{'role': 'user', 'content': 'Reply only OK.'}]
for index in range(1 if options.smoke else 2):
    request = urllib.request.Request(result['endpoint'], data=json.dumps(payload).encode(),
        headers={'x-api-key': key, 'anthropic-version': '2023-06-01',
                 'Content-Type': 'application/json', 'User-Agent': 'curl/8.10.1'})
    sample = {'sequence': index + 1, 'message_stop': False}
    try:
        response = urllib.request.urlopen(request, timeout=120)
    except urllib.error.HTTPError as error:
        body = error.read().decode(errors='replace')
        print(json.dumps({'status': error.code, 'error': body[:1500].replace(key, '[REDACTED]')}))
        raise SystemExit(1)
    with response:
        sample['status'] = response.status
        for line in response:
            if not line.startswith(b'data: '):
                continue
            event = json.loads(line[6:])
            if event.get('type') == 'message_start':
                sample['start_usage'] = event['message']['usage']
            elif event.get('type') == 'message_delta':
                sample['final_usage'] = event.get('usage', {})
            elif event.get('type') == 'message_stop':
                sample['message_stop'] = True
            elif event.get('type') == 'error':
                sample['stream_error'] = True
    result['requests'].append(sample)
    print(json.dumps(sample), flush=True)
    assert sample['message_stop'] and not sample.get('stream_error')
    time.sleep(1)
Path(__file__).with_name('evidence').mkdir(exist_ok=True)
filename = ('sub2api-daily-test-smoke.json' if options.smoke else 'sub2api-billing-validation.json')
Path(__file__).with_name('evidence').joinpath(filename).write_text(
    json.dumps(result, indent=2), encoding='utf-8')
