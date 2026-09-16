"""Real metadata-only recovery acceptance in a fresh isolated database."""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fastnews.config import load_config
from fastnews.db import connect,sync_registry
from fastnews.custom_sources import ReaderAdapter
from fastnews.editorial import create_edition
from fastnews.pipeline import scan_morning
from fastnews.research import research_product
from fastnews.source_catalog import configured_view,section_ids


def main():
    sys.stdout.reconfigure(encoding='utf8')
    dest=ROOT/'data/source_repair_20260916'/('live-'+uuid.uuid4().hex[:8]);dest.mkdir(parents=True)
    now=datetime.now(timezone.utc);day=now.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).date().isoformat()
    with closing(connect(dest/'acceptance.db',mode='live')) as conn:
        base=load_config();sync_registry(conn,base);config=configured_view(conn,base);sync_registry(conn,config)
        adapter=ReaderAdapter(config,cache_dir=str(dest/'http'))
        scans=[]
        for _ in range(2):
            scan=scan_morning(conn,config,collection_date=day,source_ids=section_ids(config,'all'),adapter=adapter,now=now)
            scans.append(scan)
            print(json.dumps({'stage':'scan','new':scan['articles_new'],'statuses':[(s['source_id'],s['status'],s['items_seen'],s['error_code']) for s in scan['sources']]},ensure_ascii=False),flush=True)
        assert scans[1]['articles_new']==0
        products=[]
        for code in config['products']:
            edition=create_edition(conn,config,product_code=code,edition_date=day,news_cutoff=now.isoformat(),market_cutoffs={})
            report=research_product(conn,config,code,edition_id=edition['edition_id'],discovery_adapter=adapter)
            row={'product_code':code,'match_counts':report['match_counts'],'fixed_sources':report['fixed_source_discovery'],
                 'usage':report['usage']}
            products.append(row)
            assert report['usage']['llm_requests']==report['usage']['web_search_requests']==report['usage']['fulltext_requests']==0
            print(json.dumps({'stage':'product','code':code,'matches':report['match_counts']},ensure_ascii=False),flush=True)
        assert conn.execute('SELECT count(*) FROM fn_decisions').fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM fn_facts').fetchone()[0]==0
        result={'is_fixture':False,'cutoff':now.isoformat(),'scans':scans,'products':products,
                'facts':0,'decisions':0,'host_tasks':0,'network_usage':adapter.stats}
    path=dest/'summary.json';path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print('Report: '+str(path),flush=True)

if __name__=='__main__':main()
