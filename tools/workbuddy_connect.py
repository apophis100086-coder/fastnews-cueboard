"""Inspect or explicitly test the official account connection."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from fastnews.workbuddy_runtime import connection_status
from fastnews.workbuddy_connection import probe,login
from fastnews.task_control import TaskControl

if __name__=='__main__':
    p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group()
    g.add_argument('--probe',action='store_true');g.add_argument('--login',action='store_true')
    p.add_argument('--model');p.add_argument('--effort');args=p.parse_args()
    if args.login:result=login()
    elif args.probe:
        from fastnews.task_control import TaskStopped
        try:result=probe(vars(args),TaskControl(180),lambda x:print(x['message'],flush=True))
        except TaskStopped:result=connection_status()
    else:result=connection_status()
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if args.probe and result.get('model')!='available':raise SystemExit(2)
