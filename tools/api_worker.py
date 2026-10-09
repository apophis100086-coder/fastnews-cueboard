"""Isolated cancellable HTTPS request; never print raw exceptions or credentials."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastnews.api_provider import post_json,scrub
from fastnews.host_provider import HostUnavailable

def main():
    packet=json.loads(sys.stdin.buffer.read(1_000_000).decode('utf-8'))
    try:
        raw=post_json(packet['url'],packet['key'],packet['body'],packet['timeout'])
        result={'result':scrub(raw,packet['key'])}
    except HostUnavailable as exc:result={'error':str(exc)}
    except Exception:result={'error':'API 网络连接失败；请检查公开 HTTPS 地址、网络和服务状态'}
    sys.stdout.buffer.write(json.dumps(result,ensure_ascii=False).encode('utf-8'))

if __name__=='__main__':main()
