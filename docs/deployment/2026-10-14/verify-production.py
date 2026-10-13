"""Read-only, sanitized post-deployment verification; never sends inference."""
import datetime as dt
import importlib.util
import json
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
spec = importlib.util.spec_from_file_location('deployment', pathlib.Path(__file__).with_name('deploy-production-62ae534.py'))
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
report = json.loads(pathlib.Path(sys.argv[1]).read_text())
current = d.inspect(d.NAME)
config = json.loads((d.ROOT / 'data/config.json').read_text())
codes = {
    'admin': d.http('/admin')[0],
    'unauthenticated_models': d.http('/v1/models')[0],
    'authenticated_models': d.http('/v1/models', config['apiKey'])[0],
    'admin_credentials': d.http('/api/admin/credentials', config['adminApiKey'])[0],
}
db = sqlite3.connect('file:' + str(d.ROOT / 'data/traces.db') + '?mode=ro', uri=True)
rows = db.execute('SELECT final_status, COUNT(*) FROM traces WHERE ts >= ? GROUP BY final_status',
                  (current['State']['StartedAt'],)).fetchall()
db.close()
result = {
    'checked_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
    'image': current['Image'],
    'expected_image': current['Image'] == report['image'],
    'running': current['State']['Running'],
    'restarts': current['RestartCount'],
    'started_at': current['State']['StartedAt'],
    'http_status': codes,
    'config_matches_stopped_backup': (d.ROOT / 'data/config.json').read_bytes() ==
        (pathlib.Path(report['backup']) / 'data/config.json').read_bytes(),
    'completed_natural_traces_since_start': dict(rows),
    'inference_sent': False,
    'rollback_image_available': json.loads(d.run('docker', 'image', 'inspect', report['previous_image']))[0]['Id'] == report['previous_image'],
}
print(json.dumps(result, indent=2))
assert result['expected_image'] and result['running'] and result['restarts'] == 0
assert codes == {'admin': 200, 'unauthenticated_models': 401, 'authenticated_models': 200, 'admin_credentials': 200}
assert result['config_matches_stopped_backup'] and result['rollback_image_available']
