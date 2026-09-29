"""Run actual public source probes; never substitutes web-search excerpts."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='data/probes/source_probe.json')
    parser.add_argument('--raw', action='store_true', help='Inspect original registered entries before parser work')
    args = parser.parse_args()
    from fastnews.config import load_config
    config = load_config()
    if not args.raw:
        from fastnews.live_sources import probe_sources
        report = probe_sources(config)
    else:
        from fastnews.network import SafeHTTPClient, NetworkError
        from fastnews.live_sources import load_live_config
        policy = load_live_config(config)['sources']
        client = SafeHTTPClient(dns_mode='cloudflare_public')
        def one(pair):
            sid, source = pair
            url = source.get('discovery_url') or source['home_url']
            row = {'source_id':sid,'url':url,'checked_at':datetime.now(timezone.utc).isoformat()}
            if sid in policy and not policy[sid]['metadata_allowed']:
                row.update(status='manual_only',error_code='source_terms_manual_only')
                print(json.dumps(row,ensure_ascii=False),flush=True)
                return row
            try:
                r = client.get(url)
                row.update(status='accessible',http_status=r.status,final_url=r.url,bytes=len(r.body),
                           retrieved_at=r.retrieved_at)
                folder = ROOT/'data/probes/listings'
                folder.mkdir(parents=True,exist_ok=True)
                (folder/(sid+'.html')).write_text(r.text,encoding='utf-8')
            except NetworkError as exc:
                row.update(status='blocked',error_code=exc.code)
            print(json.dumps(row,ensure_ascii=False),flush=True)
            return row
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(one,pair) for pair in config['sources'].items()]
            rows = [future.result() for future in as_completed(futures)]
        report = {'checked_at':datetime.now(timezone.utc).isoformat(),'sources':rows,
                  'usage':client.stats,'access_reviews':client.access_reviews}
    target = ROOT/args.output
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(target),'summary':report.get('summary'),'usage':report.get('usage')},ensure_ascii=False))

if __name__ == '__main__':
    main()
