"""One local workspace identity and port for launch, stop and agent exchange."""
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]


def settings(root=ROOT):
    path = Path(root) / 'config/workspace.yaml'
    value = yaml.safe_load(path.read_text(encoding='utf-8')) if path.exists() else {}
    port = value.get('port', 8767)
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('工作区端口需为1024—65535的整数')
    direct = value.get('agent_direct', False)
    if type(direct) is not bool:
        raise ValueError('智能体直连设置需为布尔值')
    folder = value.get('package_folder', '快讯AI看板 WorkBuddy版')
    if not isinstance(folder, str) or not folder or Path(folder).name != folder or any(c in folder for c in '/\\:'):
        raise ValueError('试用包目录名称无效')
    return dict(port=port, agent_direct=direct, package_folder=folder)


def identity(database=None):
    path = Path(database) if database else ROOT / 'data/fastnews.db'
    return hashlib.sha256(os.path.normcase(str(path.resolve())).encode('utf-8')).hexdigest()


def dashboard_address(root=ROOT):
    """Use only this workspace's recorded loopback address, including after a collision."""
    root = Path(root).resolve()
    port = settings(root)['port']
    try:
        record = json.loads((root / 'data/dashboard-runtime.json').read_text(encoding='utf-8'))
        expected = os.path.normcase(str(root / 'data/fastnews.db'))
        actual = os.path.normcase(os.path.abspath(record['workspace_id']))
        url = urlsplit(record['url'])
        if (actual == expected and url.scheme == 'http' and url.hostname == '127.0.0.1'
                and url.username is None and url.password is None
                and url.path in ('', '/') and not url.query and not url.fragment
                and url.port is not None and 1024 <= url.port <= 65535):
            port = url.port
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return port, f'http://127.0.0.1:{port}/'


def bootstrap_profile(conn, root=ROOT):
    """Portable empty packages use direct mode; never override a saved choice."""
    import json
    rows = conn.execute('SELECT profile_id,settings_json FROM fn_reader_profiles').fetchall()
    for row in rows:
        value = json.loads(row[1])
        if 'agent_direct' not in value:
            value['agent_direct'] = settings(root)['agent_direct']
            with conn:
                conn.execute('UPDATE fn_reader_profiles SET settings_json=? WHERE profile_id=?',
                             (json.dumps(value, ensure_ascii=False),row[0]))
