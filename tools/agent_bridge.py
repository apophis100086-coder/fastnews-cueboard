"""WorkBuddy 智能体 → 看板 数据回写桥（agent bridge）。

看板在 5.6.2 环境无法通过自带 CLI 调模型（认证是沙箱绑定的，CLI 读不到）。
模型能力改由 WorkBuddy 智能体（即当前对话的 agent）承担：智能体负责联网检索、
用模型提炼线索并起草初稿，本脚本把这些结果回写进看板库，看板继续负责展示与选材。

这是只读/追加式的薄封装，直接调用看板已有的 editorial / routing / writing_background
函数，不重写业务逻辑、不触碰人工选择与历史稿。

用法（在项目根目录，用包内 Python 运行）：
    runtime/python/python.exe -X utf8 tools/agent_bridge.py list-editions
    runtime/python/python.exe -X utf8 tools/agent_bridge.py route <edition_id>
    runtime/python/python.exe -X utf8 tools/agent_bridge.py pool <edition_id>
    runtime/python/python.exe -X utf8 tools/agent_bridge.py add-candidate <edition_id> <event_id> --snapshot <json文件>
    runtime/python/python.exe -X utf8 tools/agent_bridge.py save-draft <edition_id> <markdown文件>
"""
from __future__ import annotations

import argparse
import json
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "fastnews.db"
EXPORTS = ROOT / "exports"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastnews import editorial  # noqa: E402
from fastnews.config import load_config  # noqa: E402
from fastnews.db import connect  # noqa: E402
from fastnews.routing import route_product  # noqa: E402
from fastnews.source_catalog import configured_view  # noqa: E402
from fastnews.writing_background import add_candidate, pool_items  # noqa: E402


def _conn():
    return connect(DB, mode="live")


def _config(conn):
    return configured_view(conn, load_config(), "default")


def cmd_list_editions(_args):
    with closing(_conn()) as conn:
        rows = conn.execute(
            "SELECT edition_id, product_code, edition_date, news_cutoff FROM fn_editions ORDER BY created_at DESC"
        ).fetchall()
        out = [dict(r) for r in rows]
    print(json.dumps(out, ensure_ascii=False, indent=2))


def _route(conn, edition_id):
    edition = editorial.get_edition(conn, edition_id)
    config = _config(conn)
    route = route_product(
        conn, config, edition["product_code"],
        edition_date=edition["edition_date"],
        news_cutoff=edition["news_cutoff"],
        market_cutoffs=edition["market_cutoffs"],
    )
    items = []
    for group, events in route["groups"].items():
        for e in events:
            items.append({
                "event_id": e["event_id"],
                "group": group,
                "title": e.get("title"),
                "cutoff_state": e.get("cutoff_state"),
                "summary": "\n".join(x.get("text", "") for x in e.get("product_match", {}).get("excerpts", [])) or None,
                "why": e.get("product_match", {}).get("explanation"),
                "sources": [
                    {"source_name": config["sources"].get(v["metadata"].get("source_id"), {}).get("name", v["metadata"].get("source_id")),
                     "url": v.get("canonical_url"),
                     "published_at": v["metadata"].get("report_published_at") or v["metadata"].get("first_published_at")}
                    for v in e.get("metadata_versions", [])[:4]
                ],
            })
    return {"edition_id": edition_id, "product_code": edition["product_code"],
            "edition_date": edition["edition_date"], "news_cutoff": edition["news_cutoff"],
            "event_count": len(items), "events": items}


def cmd_route(args):
    with closing(_conn()) as conn:
        out = _route(conn, args.edition_id)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_pool(args):
    with closing(_conn()) as conn:
        config = _config(conn)
        items = pool_items(conn, config, args.edition_id)
    print(json.dumps(items, ensure_ascii=False, indent=2))


def cmd_add_candidate(args):
    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    with closing(_conn()) as conn:
        config = _config(conn)
        result = add_candidate(conn, config, args.edition_id, args.event_id, snapshot=snapshot)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_save_draft(args):
    content = Path(args.markdown).read_text(encoding="utf-8")
    edition_id = args.edition_id
    with closing(_conn()) as conn:
        edition = editorial.get_edition(conn, edition_id)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    EXPORTS.mkdir(parents=True, exist_ok=True)
    name = f"draft-{edition['product_code']}-{edition_id[:12]}-{stamp}.md"
    path = EXPORTS / name
    path.write_text(content, encoding="utf-8")
    print(json.dumps({"saved": str(path), "edition_id": edition_id,
                      "product_code": edition["product_code"]}, ensure_ascii=False, indent=2))


def cmd_pending_writes(_args):
    """列出看板写作流水线中等待智能体响应的模型步骤。

    使用 claim 接手完整 request，再用 respond 提交包含全部绑定字段的响应。
    不直接覆盖响应文件；看板继续业务检查及保存。参见 docs/AGENT_BRIDGE.md。
    """
    from fastnews.agent_runner import pending_requests
    print(json.dumps(pending_requests(), ensure_ascii=False, indent=2))


def cmd_claim(args):
    from fastnews.agent_runner import claim_request
    meta=json.loads(Path(args.request).read_text(encoding='utf-8'))
    if args.host and meta.get('host','workbuddy')!=args.host:raise ValueError('请求不属于当前客户端入口')
    print(json.dumps(claim_request(args.request), ensure_ascii=False, indent=2))


def cmd_respond(args):
    from fastnews.agent_runner import submit_response
    raw=json.loads(Path(args.response).read_text(encoding='utf-8'))
    print(json.dumps(submit_response(args.request,raw),ensure_ascii=False,indent=2))


def cmd_status(_args):
    from urllib.request import build_opener,ProxyHandler
    from urllib.parse import quote
    from tools.open_dashboard import check_service,URL
    from fastnews.agent_runner import pending_requests
    status=check_service()
    result={'service':status,'url':URL,'pending':pending_requests()}
    if status=='ready':
        opener=build_opener(ProxyHandler({}))
        with opener.open(URL+'api/state',timeout=3) as response:state=json.load(response)
        jid=state.get('active_job');result['active_job']=jid
        if jid:
            with opener.open(URL+'api/job?id='+quote(jid),timeout=3) as response:job=json.load(response)
            result['job']={k:job.get(k) for k in ('job_id','status','action','workflow','stop_requested','error')}
    print(json.dumps(result,ensure_ascii=False,indent=2))


def cmd_wait(args):
    import time
    from fastnews.agent_runner import pending_requests
    end=time.monotonic()+max(1,min(args.seconds,20))
    while time.monotonic()<end:
        if any(x['run_id']==args.run_id for x in pending_requests()):break
        time.sleep(.5)
    cmd_status(args)


def dashboard_client():
    from fastnews.dashboard_client import DashboardClient
    from tools.open_dashboard import URL
    return DashboardClient(URL, DB)


def cmd_products(_args):
    print(json.dumps(dashboard_client().products(), ensure_ascii=False, indent=2))


def cmd_start(args):
    result = dashboard_client().start(
        'research' if args.command == 'start-research' else 'digest', args.host,
        product=getattr(args, 'product', None), date=args.date,
        cutoff=getattr(args, 'cutoff', None), keywords=getattr(args, 'keywords', None),
        profile=args.profile)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_result(args):
    print(json.dumps(dashboard_client().result(args.job_id), ensure_ascii=False, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser('products', help='列出看板产品及当前日期截点').set_defaults(func=cmd_products)
    for command in ('start-research', 'start-morning'):
        p = sub.add_parser(command, help='从当前客户端对话发起看板任务')
        p.add_argument('--host', required=True, choices=['workbuddy', 'codex'])
        p.add_argument('--profile', default='default')
        p.add_argument('--date')
        if command == 'start-research':
            p.add_argument('--product', required=True)
            p.add_argument('--cutoff')
            p.add_argument('--keywords', nargs='+')
        p.set_defaults(func=cmd_start)
    p = sub.add_parser('result', help='核对任务及看板实际保存结果')
    p.add_argument('--job-id', required=True)
    p.set_defaults(func=cmd_result)

    sub.add_parser("list-editions").set_defaults(func=cmd_list_editions)

    p = sub.add_parser("route")
    p.add_argument("edition_id")
    p.set_defaults(func=cmd_route)

    p = sub.add_parser("pool")
    p.add_argument("edition_id")
    p.set_defaults(func=cmd_pool)

    p = sub.add_parser("add-candidate")
    p.add_argument("edition_id")
    p.add_argument("event_id")
    p.add_argument("--snapshot", required=True)
    p.set_defaults(func=cmd_add_candidate)

    p = sub.add_parser("save-draft")
    p.add_argument("edition_id")
    p.add_argument("markdown")
    p.set_defaults(func=cmd_save_draft)

    sub.add_parser("pending-writes").set_defaults(func=cmd_pending_writes)
    sub.add_parser('status').set_defaults(func=cmd_status)
    p=sub.add_parser('claim');p.add_argument('request');p.add_argument('--host',choices=['workbuddy','codex']);p.set_defaults(func=cmd_claim)
    p=sub.add_parser('respond');p.add_argument('request');p.add_argument('response');p.set_defaults(func=cmd_respond)

    p=sub.add_parser('wait');p.add_argument('--run-id',required=True);p.add_argument('--seconds',type=int,default=20);p.set_defaults(func=cmd_wait)

    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
