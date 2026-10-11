import json
import sqlite3

with sqlite3.connect('file:/home/deploy/kiro-billing-test-20261010/data/traces.db?mode=ro', uri=True) as connection:
    connection.row_factory = sqlite3.Row
    print(json.dumps([dict(row) for row in connection.execute(
        'SELECT model, final_status, input_tokens, output_tokens, cache_creation_tokens, '
        'cache_read_tokens, credits, usage_source FROM traces ORDER BY ts_epoch DESC LIMIT 2')]))
