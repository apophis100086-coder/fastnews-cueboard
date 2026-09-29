"""Read-only row fingerprints for maintenance acceptance; never prints material."""
from pathlib import Path
import hashlib
import json
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]


def snapshot(path):
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        result = {}
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            rows = sorted([list(row) for row in conn.execute('SELECT * FROM "' + name.replace('"', '""') + '"')], key=repr)
            result[name] = {'count': len(rows), 'sha256': hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
        return result


if __name__ == '__main__':
    dest = ROOT / 'data' / 'workflow_acceptance'
    dest.mkdir(exist_ok=True)
    label = sys.argv[1] if len(sys.argv) > 1 else 'before'
    path = dest / ('preservation.' + label + '.json')
    result = snapshot(ROOT / 'data' / 'fastnews.db')
    if path.exists():
        raise SystemExit('Existing audit preserved; use a new label')
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v['count'] for k, v in result.items()}, ensure_ascii=False))
