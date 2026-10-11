import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

root = Path('/home/deploy/kiro-billing-test-20261010')


def validate(result):
    assert all(sample.get('message_stop') and 'final_usage' in sample for sample in result['requests'])
    assert all(sample['start_usage']['input_tokens'] == 0 for sample in result['requests'])
    assert all(all(value == sample['final_usage'][name]
                   for name, value in sample['sub2api_positive_only_parser_usage'].items())
               for sample in result['requests'])
    assert len(result['trace_usage']) == len(result['requests']) == 2
    for sample, trace in zip(result['requests'], reversed(result['trace_usage'])):
        usage = sample['final_usage']
        assert usage['input_tokens'] == trace['input_tokens']
        assert usage['output_tokens'] == trace['output_tokens']
        assert usage['cache_creation_input_tokens'] == trace['cache_creation_tokens']
        assert usage['cache_read_input_tokens'] == trace['cache_read_tokens']
    assert result['requests'][1]['final_usage']['cache_read_input_tokens'] > 0


if '--check-only' in sys.argv:
    validate(json.loads((root / 'validation.json').read_text()))
    print(json.dumps({'saved_results_validated': True, 'additional_inference_requests': 0}))
    sys.exit(0)

config = json.loads((root / 'data/config.json').read_text())
base = 'http://127.0.0.1:8993'
headers = {'x-api-key': config['apiKey'], 'anthropic-version': '2023-06-01'}
result = {'sub2apiUsageCompat': config['sub2apiUsageCompat'], 'requests': []}

try:
    urllib.request.urlopen(base + '/v1/models', timeout=10)
    raise AssertionError('unauthenticated model access unexpectedly succeeded')
except urllib.error.HTTPError as error:
    assert error.code == 401
    result['unauthenticated_status'] = error.code

with urllib.request.urlopen(urllib.request.Request(base + '/v1/models', headers=headers), timeout=60) as response:
    models = json.load(response)['data']
    result['models_status'] = response.status
ids = [item['id'] for item in models]
model = next((item for item in ids if 'sonnet' in item.lower() and 'thinking' not in item.lower()), None)
assert model, 'no Sonnet model found'
result['model'] = model
session = str(uuid.uuid4())
payload = {
    'model': model, 'stream': True, 'max_tokens': 32,
    'metadata': {'user_id': json.dumps({'session_id': session})},
    'messages': [{'role': 'user', 'content': [{
        'type': 'text',
        'text': ('This is a repeated billing validation paragraph. ' * 180) + '\nReply only OK.',
        'cache_control': {'type': 'ephemeral'},
    }]}],
}
for index in range(2):
    request = urllib.request.Request(base + '/v1/messages',
        data=json.dumps(payload).encode(), headers={**headers, 'Content-Type': 'application/json'})
    sample = {'sequence': index + 1, 'message_stop': False}
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            sample['status'] = response.status
            for line in response:
                if not line.startswith(b'data: '):
                    continue
                event = json.loads(line[6:])
                if event.get('type') == 'message_start':
                    sample['start_usage'] = event['message']['usage']
                elif event.get('type') == 'message_delta':
                    sample['final_usage'] = event['usage']
                elif event.get('type') == 'message_stop':
                    sample['message_stop'] = True
                elif event.get('type') == 'error':
                    sample['stream_error'] = True
    except urllib.error.HTTPError as error:
        sample['status'] = error.code
    if 'start_usage' in sample and 'final_usage' in sample:
        parsed = dict(sample['start_usage'])
        for name, value in sample['final_usage'].items():
            if name in parsed and isinstance(value, int) and value > 0:
                parsed[name] = value
        sample['sub2api_positive_only_parser_usage'] = parsed
    result['requests'].append(sample)
    time.sleep(1)

time.sleep(2)
with sqlite3.connect(f"file:{root / 'data/traces.db'}?mode=ro", uri=True) as connection:
    connection.row_factory = sqlite3.Row
    result['trace_usage'] = [dict(row) for row in connection.execute(
        'SELECT model, final_status, input_tokens, output_tokens, cache_creation_tokens, '
        'cache_read_tokens, credits, usage_source FROM traces ORDER BY ts_epoch DESC LIMIT 2')]
(root / 'validation.json').write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
validate(result)
