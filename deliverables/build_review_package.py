"""Create a portable review archive without changing the running project data."""
from collections import Counter
from contextlib import closing
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'deliverables'
STAMP = datetime.now().astimezone().strftime('%Y%m%d_%H%M%S')
ARCHIVE = OUTPUT / f'华泰柏瑞快讯AI看板_完整审阅包_{STAMP}.zip'
PREFIX = f'HuataiPB-Fastnews-Review-{STAMP}'
SKIP_DIRS = {'.venv', 'venv', '.git', 'node_modules', '__pycache__',
             '.pytest_cache', '.mypy_cache', '.ruff_cache', 'deliverables'}
EXCLUDED = []
MANIFEST = []
DATABASES = []


def reason_for(path):
    name = path.name.lower()
    if name.startswith('.env') and name not in {'.env.example', '.env.sample', '.env.template'}:
        return 'local environment credentials'
    if name in {'auth.json', 'credentials.json', 'secrets.json'} or path.suffix.lower() in {'.pem', '.key', '.p12', '.pfx'}:
        return 'credential material'
    if name in {'dashboard.lock', 'dashboard-runtime.json'} or name.endswith(('.pid', '.pyc', '.pyo', '-wal', '-shm', '-journal')):
        return 'machine runtime state or cache; SQLite committed state is in the backup snapshot'
    return None


def files_to_pack():
    result = []
    for base, dirs, names in os.walk(ROOT):
        retained = []
        for name in sorted(dirs):
            path = Path(base) / name
            if name in SKIP_DIRS or path.is_symlink():
                EXCLUDED.append({'path': path.relative_to(ROOT).as_posix() + '/', 'reason': 'runtime, cache, archive output or symbolic link'})
            else:
                retained.append(name)
        dirs[:] = retained
        for name in sorted(names):
            path = Path(base) / name
            rel = path.relative_to(ROOT).as_posix()
            why = 'symbolic link outside archive scope' if path.is_symlink() else reason_for(path)
            if why:
                EXCLUDED.append({'path': rel, 'reason': why})
            else:
                result.append((rel, path))
    return sorted(result)


def fingerprint(conn):
    tables = {}
    for name, in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        quoted = '"' + name.replace('"', '""') + '"'
        rows = sorted([list(row) for row in conn.execute('SELECT * FROM ' + quoted)], key=repr)
        encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=repr).encode('utf-8')
        tables[name] = {'count': len(rows), 'sha256': hashlib.sha256(encoded).hexdigest()}
    return tables


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')


def add(z, rel, content, origin='project_file'):
    z.writestr(PREFIX + '/' + rel, content)
    MANIFEST.append({'path': rel, 'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest(), 'origin': origin})


def main():
    files = files_to_pack()
    with tempfile.TemporaryDirectory(prefix='fastnews-review-') as temp, zipfile.ZipFile(ARCHIVE, 'x', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for index, (rel, path) in enumerate(files):
            if path.suffix.lower() in {'.db', '.sqlite', '.sqlite3'}:
                copy = Path(temp) / f'{index}.db'
                with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=30)) as source, closing(sqlite3.connect(copy)) as target:
                    # Pin a read snapshot before the online backup; no writes to source.
                    source.execute('BEGIN')
                    source.execute('SELECT count(*) FROM sqlite_master').fetchone()
                    source.backup(target)
                    checks = [row[0] for row in target.execute('PRAGMA quick_check')]
                    if checks != ['ok']:
                        raise RuntimeError(f'Database integrity failure: {rel}')
                    record = {'path': rel, 'integrity': 'ok', 'method': 'sqlite_online_backup'}
                    if rel == 'data/fastnews.db':
                        original, copied = fingerprint(source), fingerprint(target)
                        if original != copied:
                            raise RuntimeError('Production snapshot row fingerprints do not match')
                        record['tables'] = copied
                        record['matches_source_read_snapshot'] = True
                    DATABASES.append(record)
                add(z, rel, copy.read_bytes(), 'sqlite_consistent_snapshot')
            else:
                add(z, rel, path.read_bytes())

        visual_references = [
            ('01_蓝红纸感与数字图形.jpg', Path('C:/Users/86150/AppData/Local/Temp/codex-clipboard-8bbcdfe1-45ed-4227-a3a7-0aba7e255213.jpg')),
            ('02_分步信息与视觉层级.jpg', Path('C:/Users/86150/AppData/Local/Temp/codex-clipboard-66e065f5-83ee-434b-9851-2d84567ef3c7.jpg')),
        ]
        for name, path in visual_references:
            if not path.is_file():
                raise FileNotFoundError(f'User-provided style reference is missing: {name}')
            add(z, 'REVIEW/用户提供的UI风格参考/' + name, path.read_bytes(), 'user_provided_visual_reference')

        production = next(d for d in DATABASES if d['path'] == 'data/fastnews.db')
        counts = {name: item['count'] for name, item in production['tables'].items()}
        summary = {
            'packaged_at': datetime.now().astimezone().isoformat(),
            'production_database': 'data/fastnews.db',
            'production_table_counts': counts,
            'production_snapshot_matches_original': True,
            'database_snapshots': DATABASES,
            'prior_validation': {'python_tests': 416, 'frontend_tests': 29, 'original_manifest_files': 40, 'rerun_during_packaging': False},
            'current_source_policy': {'automatic_entrances': 13, 'manual_references': 19, 'note': '入口数不是独立网站数；真实结果以每次采集记录为准'},
            'runtime_observation': '打包时原 .venv 指向的旧 Python 基础路径无法启动；本次使用打包工具运行时制作 ZIP，未更改项目环境。审阅者请按依赖清单重建自己的环境。',
            'excluded': EXCLUDED,
        }
        add(z, 'REVIEW/当前快照与数据库校验.json', json_bytes(summary), 'review_metadata')

        start = f'''# 先读我：华泰柏瑞快讯 AI 线索看板审阅包

打包时间：{summary['packaged_at']}。

这是当前项目的代码、配置、文档、测试、数据快照、导出与历史验收资料。目标是让另一个对话窗口基于既有实现检查问题、提出增量修改意见。此包不是从零建设的需求草案，也不表示可以直接运行所有旧阶段演示。

## 建议阅读顺序

1. `REVIEW/给审阅窗口的提示词.md`：用户希望怎样审阅。
2. `AGENTS.md`：业务和数据边界。
3. `docs/IMPLEMENTATION_STATUS.md` 开头的最新记录，以及 `docs/UI_REDESIGN_ACCEPTANCE.md`。
4. `RUNNING.md`：当前使用流程；`docs/READER_HOST_ACCEPTANCE.md`：宿主调用与历史验收。
5. `fastnews/static/`：实际 HTML、JavaScript、样式；`fastnews/`：后端；`config/`、`db/`、`tests/`、`tools/`：配置、迁移和验证。
6. 按需读取其他材料，特别是来源、候选池、日期截点和交接包的实现。

`CODEX_MASTER_HANDOFF.md`、原始 `README.md` 及很多验收文档包含早期阶段记录。它们保留原样；“尚未实现”“不要先做 UI”等历史描述不能覆盖最新工作状态。判断当前能力请结合代码、最新文档和验收日志，不能仅相信说明文字。

## 当前工作流

晨报用于速览；选定产品后，沿领域网站与关键词两路寻找写作线索；使用者将线索加入或移出候选池，自主多选、排序、补充成交量/资金等信息，再保存和导出快讯背景 Markdown。AI 不自动选题、勾选、成稿或发布。

信息源扩展已按用户要求收口。13 个入口用于日常自动任务，19 个保留原站供手动查阅及补充背景。同类渠道只补充公开资讯，不能冒充原站监管公告、授权数据或完整覆盖。WorkBuddy、Wind 尚未接通。

本轮 UI 已采用蓝红与浅色纸感、三步导航、分类条形图、双路检索说明和候选/背景并排布局。`REVIEW/用户提供的UI风格参考/` 中两张海报是用户提供的视觉参考，仅用于研究配色、排版和信息层级，不能复制品牌或宣传数据。

## 数据说明

正式当前数据是 `data/fastnews.db`。它使用 SQLite 在线备份制作一致性快照，包含已提交内容；快照与源库同一读取时点的 {len(counts)} 张表逐表行数及内容指纹一致。

- 文章 {counts['fn_articles']} 篇，版本 {counts['fn_article_versions']} 个。
- 产品期次 {counts['fn_editions']} 个，研究报告 {counts['fn_reading_reports']} 份。
- 人工决定版本 {counts['fn_decisions']} 个，候选池记录 {counts['fn_candidate_pool']} 条，导出记录 {counts['fn_exports']} 个。
- 来源方案 {counts['fn_reader_profiles']} 个，来源偏好记录 {counts['fn_source_preferences']} 条。

这些是打包时计数，可能高于上一阶段文档的计数。`data/backups/` 和其他验收数据库属于历史备份或测试副本，不应覆盖正式快照。`data/ui_redesign_20260916/preview.db` 内有明确标注的 UI 测试补充，不能作为真实市场材料。正式导出在 `exports/`；验收目录中的导出是测试证据。

本包按用户要求保留数据、人工补充、历史导出及验收原始材料，不是脱敏演示包。没有读取或打包项目目录之外的账户登录目录；仅额外加入用户提供的两张参考图。

## 可复现环境

需要 Python 3.11 或以上，以及 `pyproject.toml`、`requirements.lock` 指定的依赖。`.venv` 是机器专用环境，不随包复制。打包时该环境所引用的旧 Python 路径无法启动，因此本次不声称重新跑过应用测试；原 416 项 Python 与 29 项前端的通过日志已随包保留。

在解压目录重建环境的示例（按所在系统选择 Python 命令）：

```text
python -m venv .venv
# Windows
.venv\\Scripts\\python.exe -m pip install -r requirements.lock
.venv\\Scripts\\python.exe -m unittest discover -s tests
node --test tests/ui_daily_test.cjs
```

如要尝试页面，优先复制此包中的数据库到独立验收目录，避免修改当前快照；先读 `RUNNING.md` 和 CLI 帮助。原桌面启动项、Codex 登录状态及其它本机授权不会随 ZIP 转移。仅做静态审阅时无需安装或登录。

## 打包范围与校验

保留所有项目业务文件、历史备份、参考资料和测试证据；排除虚拟环境、依赖目录、编译缓存、进程/锁状态和凭据文件。SQLite WAL/SHM 不单独携带，已提交状态已并入一致性快照。完整排除清单与数据库校验见 `REVIEW/当前快照与数据库校验.json`。

`REVIEW_PACKAGE_MANIFEST.json` 列出所有内容文件的路径、大小和 SHA-256。原 `MANIFEST.json` 仍仅验证最初 40 个交接文件，不是本次完整项目清单。ZIP 已通过 CRC 及逐项内容哈希核验。
'''
        prompt = '''请检查这个 ZIP 中现有的“华泰柏瑞快讯 AI 线索看板”，先审阅并提出修改意见，不直接改文件。

先读“00_先读我_审阅说明.md”，再读 AGENTS.md、docs/IMPLEMENTATION_STATUS.md 最新段落、RUNNING.md、docs/UI_REDESIGN_ACCEPTANCE.md、docs/READER_HOST_ACCEPTANCE.md，并查看相关实际源码。早期交接文件中的状态以当时为准，不要误判项目尚未实现。

我的实际流程是：
1. 晨报只用于上班时速览当天重点。
2. 选定产品，沿对应细分领域网站重点检索，再叠加关键词检索，得到当日快讯写作线索。这是主要功能。
3. 自主把线索加入或移出候选池，选择若干条并补充成交量、资金增持或其他亮点，导出快讯背景 MD，交给独立写作窗口。

信息源扩展到此收口：可接入的同类较权威渠道用于自动任务，不能接入的保留原站，供用户手动查阅并加入补充背景。请评估现有覆盖说明是否清楚、是否存在错误行为；不把“继续接通所有网站”设为前置目标。

请重点检查：
- 上述三步是否顺畅，关键操作、异常状态、返回与恢复是否清楚。
- 实际 UI、排版、文字层级、可视化和移动适配是否适合日常使用；参考 REVIEW 内海报的风格，但保持华泰柏瑞项目身份与工具可读性。
- 产品定向/关键词检索是否实际执行，结果是否有明确出处、日期、覆盖缺口和产品关系。
- 候选池增减、多选、排序、草稿恢复、人工补充及 MD 导出是否一致，是否可能丢失或串用不同产品/期次的数据。
- 日期、时区、截点、首次公开时间、统计期、未来实施计划和未知字段是否正确区分。
- 访问控制、URL 安全、预算、调用失败处理、来源隔离、数据库迁移、环境启动和测试是否存在真实缺陷。

建议基于现有 Python + SQLite + YAML + 原生前端做增量改进，保留现有数据库、来源偏好、人工选择和历史导出。AI 不替我选题，不默认勾选，不自动成稿或发布。WorkBuddy、Wind 仍未接通；登记网址、计划、搜索摘要、读取原文和核实事实是不同状态。

请按严重程度给出有证据的问题清单，每项说明文件/位置、触发条件、用户影响、建议修改和验收方式。分清“确认的缺陷”“建议改善的体验”“需要进一步验证的疑点”。能运行时再报告实际验证结果，不能运行时说明静态审阅范围；不要虚构成功测试。最后给出最值得先做的 3—5 项，不必为了数量凑问题。
'''
        add(z, '00_先读我_审阅说明.md', start.encode('utf-8'), 'review_guide')
        add(z, 'REVIEW/给审阅窗口的提示词.md', prompt.encode('utf-8'), 'review_prompt')
        manifest = {'created_at': summary['packaged_at'], 'project_files': len(files), 'content_files': len(MANIFEST), 'files': MANIFEST,
                    'note': 'This manifest excludes its own hash to avoid self-reference.'}
        z.writestr(PREFIX + '/REVIEW_PACKAGE_MANIFEST.json', json_bytes(manifest))

    with zipfile.ZipFile(ARCHIVE) as z:
        if z.testzip() is not None:
            raise RuntimeError('ZIP CRC verification failed')
        assert len(z.namelist()) == len(set(z.namelist()))
        for item in MANIFEST:
            content = z.read(PREFIX + '/' + item['path'])
            if hashlib.sha256(content).hexdigest() != item['sha256'] or len(content) != item['bytes']:
                raise RuntimeError('Archive content hash mismatch: ' + item['path'])
        original_manifest = json.loads(z.read(PREFIX + '/MANIFEST.json'))
        for item in original_manifest['files']:
            content = z.read(PREFIX + '/' + item['path'])
            if hashlib.sha256(content).hexdigest() != item['sha256']:
                raise RuntimeError('Original handoff changed: ' + item['path'])

    result = {'archive': str(ARCHIVE), 'size_bytes': ARCHIVE.stat().st_size,
              'size_mb': round(ARCHIVE.stat().st_size / 1048576, 2),
              'project_files': len(files), 'zip_entries': len(MANIFEST) + 1,
              'database_snapshots_verified': len(DATABASES), 'production_tables_preserved': len(counts),
              'sha256': hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(),
              'crc_verified': True, 'content_hashes_verified': True, 'original_handoff_verified': len(original_manifest['files'])}
    ARCHIVE.with_suffix('.verification.json').write_bytes(json_bytes(result))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
