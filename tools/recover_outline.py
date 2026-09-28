"""Append a reviewed version from the known outline-context failure; no model call.

Back up the database before use. Existing selections and failed runs are retained.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fastnews import db
from fastnews.outline_recovery import recover_failed_outline


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--run-id',required=True)
    args=parser.parse_args()
    if not args.db.is_file():raise ValueError('需要已有数据库，恢复操作不创建空库')
    with closing(db.connect(args.db,mode='live')) as conn:
        version=recover_failed_outline(conn,args.run_id)
        print(json.dumps(dict(version_id=version['version_id'],status=version['checks']['status'],
            errors=version['checks']['errors'],new_model_calls=0),ensure_ascii=False))
