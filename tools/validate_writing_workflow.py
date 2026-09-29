"""Separate-store acceptance. --live spends the existing Codex login quota once."""
import argparse
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastnews.config import load_config
from fastnews.db import connect, sync_registry
from fastnews.editorial import create_edition
from fastnews.host_provider import HostResult
from fastnews.metadata import normalize_record
from fastnews.pipeline import store_record
from fastnews.reading_reports import generate_report
from fastnews.source_catalog import configured_view


def fixture_runner(host, prompt, schema, **kwargs):
    payload = json.loads(prompt.split('INPUT_JSON:\n')[1])
    ref = payload['items'][0]['id']
    return HostResult({'items': [
        {'title': '合成验收：平台云业务的新进展', 'summary': '这是一条用于界面验收的合成研究摘要，描述平台云业务出现新的使用场景。',
         'why': '对应恒生科技的平台经营、云业务观察变量；不是当前持仓证明。', 'category': 'technology', 'source_refs': [ref]},
        {'title': '合成验收：南向资金与市场参与', 'summary': '合成材料记录市场资金环境变化，不推断资金专门买入科技产品。',
         'why': '对应港股科技资产的市场配置环境。', 'category': 'hk', 'source_refs': [ref]},
        {'title': '合成验收：需要保留的业务不确定性', 'summary': '合成的反向信息，提醒核对业务计划与实际实施阶段。',
         'why': '保留与主线有关的不确定性和相反证据。', 'category': 'technology', 'source_refs': [ref]}],
        'web_sources': [], 'coverage': [], 'gaps': ['合成浏览器验收，不是真实新闻。']},
        {'is_fixture': True, 'host_tasks': 0, 'input_tokens': 0, 'output_tokens': 0}, {'web_actions': 0})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    directory = ROOT / 'data' / 'workflow_acceptance'
    directory.mkdir(exist_ok=True)
    path = directory / ('product.live.db' if args.live else 'workflow.fixture.db')
    with closing(connect(path, mode='live' if args.live else 'fixture')) as conn:
        base = load_config()
        sync_registry(conn, base)
        if not args.live:
            record = normalize_record({'source_id': 'cls_morning', 'title': '合成验收：今日科技与资金摘要',
                'lead': '合成云业务、AI应用与南向资金材料；不是市场事实。',
                'url': 'https://example.org/workflow-fixture', 'report_published_at': '2026-09-14T08:00:00+08:00'}, is_fixture=True)
            with conn:
                store_record(conn, record, base, '2026-09-14T09:00:00+08:00')
        config = configured_view(conn, base)
        today = datetime.now(ZoneInfo('Asia/Shanghai'))
        day = today.date().isoformat() if args.live else '2026-09-15'
        cutoff = today.isoformat() if args.live else '2026-09-14T23:59:59+08:00'
        edition = create_edition(conn, config, product_code='513130', edition_date=day,
                                 news_cutoff=cutoff, market_cutoffs={})
        extra = {} if args.live else {'runner': fixture_runner}
        report = generate_report(conn, config, 'default', 'product', day, edition=edition,
            extra_keywords=['AI应用', '云业务'], progress=lambda x: print(json.dumps(x, ensure_ascii=False), flush=True), **extra)
        target = directory / ('live-report.json' if args.live else 'fixture-report.json')
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'database': str(path), 'edition_id': edition['edition_id'], 'status': report['status'],
            'report_path': str(target), 'items': len(report['output']['items']),
            'query_plan_count': len(report['search_plan']['queries']),
            'web_actions': report['output']['web_actions'], 'usage': report['usage'],
            'facts': conn.execute('SELECT count(*) FROM fn_facts').fetchone()[0],
            'decisions': conn.execute('SELECT count(*) FROM fn_decisions').fetchone()[0]}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
