"""Opt-in live acceptance using public metadata and the logged-in host quota.

Example: .venv\Scripts\python.exe tools/validate_reader_live.py --day 2026-09-15
Results go to a separate database; no human choices are made.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fastnews.config import load_config
from fastnews.db import connect, sync_registry
from fastnews.editorial import create_edition
from fastnews.reading_reports import generate_report
from fastnews.source_catalog import configured_view


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--day',required=True)
    parser.add_argument('--product')
    parser.add_argument('--cutoff')
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    path=ROOT/'data'/'reader-live-acceptance.db'
    if not path.exists():
        with closing(sqlite3.connect(str(ROOT/'data'/'fastnews.db'))) as source, closing(sqlite3.connect(str(path))) as dest:
            source.backup(dest)
    with closing(connect(path)) as conn:
        config=configured_view(conn,load_config(),'default')
        sync_registry(conn,config)
        edition=None
        if args.product:
            edition=create_edition(conn,config,product_code=args.product,edition_date=args.day,
                news_cutoff=args.cutoff or args.day+'T23:59:59+08:00',market_cutoffs={})
        report=generate_report(conn,config,'default','product' if args.product else 'morning',args.day,
            edition=edition,progress=lambda item: print(json.dumps(item,ensure_ascii=False),flush=True))
        target=ROOT/'data'/('reader-live-'+(args.product or 'morning')+'.json')
        target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'report':str(target),'status':report['status'],
            'items':len(report['output']['items']),'web_actions':report['output']['web_actions'],
            'visited_urls':len(report['output']['visited_urls']),'usage':report['usage'],
            'titles':[r['title'] for r in report['output']['items']]},ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
