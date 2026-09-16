"""Explicit four-product live acceptance in a new, isolated database.

Uses only the already reviewed fixed-source metadata adapters. Never calls a
model, paid search, fulltext endpoint, or writes human choices in the user DB.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fastnews.config import load_config
from fastnews.db import connect, sync_registry
from fastnews.editorial import create_edition
from fastnews.providers import NoneLLMProvider, NoneSearchProvider
from fastnews.research import research_product


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date',required=True)
    parser.add_argument('--cutoff',required=True,help='Explicit aware news cutoff; no later than current clock')
    parser.add_argument('--output',default='data/product_acceptance')
    parser.add_argument('--require-matches',action='store_true',help='Exit nonzero if any product has no core match; keep all real results')
    args = parser.parse_args()
    destination = Path(args.output)/('live_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:6])
    destination.mkdir(parents=True)
    config = load_config()
    reports, rows = [], []
    with closing(connect(destination/'acceptance.db',mode='live')) as conn:
        sync_registry(conn,config)
        for code in config['products']:
            edition = create_edition(conn,config,product_code=code,edition_date=args.date,news_cutoff=args.cutoff,market_cutoffs={})
            report = research_product(conn,config,code,edition_id=edition['edition_id'],
                                      llm_provider=NoneLLMProvider(),search_provider=NoneSearchProvider())
            (destination/(code+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            reports.append(report)
            core = [e for e in report['results'] if (e.get('product_match') or {}).get('lane')=='core']
            row = {'product_code':code,'match_counts':report['match_counts'],
                   'core_titles':[e['title'] for e in core],
                   'fixed_source_requests':report['usage']['fixed_source_requests'],
                   'web_search_requests':report['usage']['web_search_requests'],
                   'llm_requests':report['usage']['llm_requests'],
                   'network_usage':report['usage']['network_usage']}
            rows.append(row)
            assert report['usage']['llm_requests']==report['usage']['web_search_requests']==report['usage']['fulltext_requests']==0
            assert report['usage']['search_requests'] <= config['runtime']['second_round']['max_search_requests']
            assert report['researched_event_count'] <= config['runtime']['second_round']['max_event_candidates']
            assert all(e['product_match']['excerpts'] and e['source_chain'] for e in core)
            print(json.dumps(row,ensure_ascii=False),flush=True)
        checks = {name:conn.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in ['fn_decisions','fn_facts']}
        checks['fixture_versions'] = conn.execute('SELECT count(*) FROM fn_article_versions WHERE is_fixture=1').fetchone()[0]
        assert not any(checks.values())
    all_matched = all(r['match_counts']['core'] > 0 for r in reports)
    summary = {'is_fixture':False,'date':args.date,'news_cutoff':args.cutoff,
               'all_four_have_matches':all_matched,'rows':rows,'no_auto_facts_or_decisions':checks,
               'note':'真实列表线索，不等于已核实事实、最新持仓映射或完整来源覆盖。空结果按实保留。'}
    path = destination/'summary.json'
    path.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Acceptance report: '+str(path.resolve()),flush=True)
    return 1 if args.require_matches and not all_matched else 0


if __name__=='__main__':
    raise SystemExit(main())
