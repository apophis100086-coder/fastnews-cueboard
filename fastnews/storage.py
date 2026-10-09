"""Verified, portable snapshots. Restore only into an unused project copy."""
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path,PurePosixPath
import re
import sqlite3
import tempfile
import uuid
import zipfile
from zoneinfo import ZoneInfo

from .workspace import ROOT

RESOURCES=('uploads','writing_samples','writing_templates')
MAX_BYTES=512*1024*1024


def browser_drafts(value):
    if not isinstance(value,dict) or len(json.dumps(value).encode())>600_000:
        raise ValueError('浏览器草稿备份超过容量，请先把人工修改另存为稿件版本')
    pattern=r'(?:last-edition|reader-profile|[A-Za-z0-9_-]{1,100}:(?:draft|order|direct-writing-editor|draft-edit:[A-Za-z0-9_-]{1,100}))'
    if any(not re.fullmatch(pattern,k) or not isinstance(v,str) for k,v in value.items()):
        raise ValueError('浏览器草稿包含无效字段')
    return value


def backup(database, *, exports=None, root=ROOT, drafts=None):
    database=Path(database).resolve();root=Path(root).resolve()
    data=database.parent;exports=Path(exports or root/'exports').resolve()
    destination=data/'backups';destination.mkdir(parents=True,exist_ok=True)
    identifier=datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8]
    path=destination/('fastnews-backup-'+identifier+'.zip')
    temp_path=path.with_suffix('.partial')
    manifest=dict(format='fastnews-user-backup-v1',created_at=datetime.now(timezone.utc).isoformat(),
        original_root=str(root),original_database=str(database),original_exports=str(exports),
        credentials_included=False,files=[],browser_drafts_included=bool(drafts))
    def add(bundle,name,raw):
        if sum(x['size'] for x in manifest['files'])+len(raw)>MAX_BYTES:raise ValueError('本次备份超过 512 MB，请先整理导出文件后重试')
        bundle.writestr(name,raw)
        manifest['files'].append(dict(path=name,size=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
    def read_file(file):
        if file.stat().st_size>MAX_BYTES:raise ValueError('单个文件超过 512 MB，请先整理导出文件后重试')
        return file.read_bytes()
    try:
        with tempfile.TemporaryDirectory(prefix='fastnews-snapshot-') as folder:
            snapshot=Path(folder)/'fastnews.db'
            with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(snapshot)) as out:
                source.backup(out)
                if out.execute('PRAGMA quick_check').fetchone()[0]!='ok' or out.execute('PRAGMA foreign_key_check').fetchone():
                    raise ValueError('备份完整性检查未通过，原数据保留')
                manifest['counts']={table:out.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                    for table in ('fn_editions','fn_decisions','fn_writing_versions','fn_documents')}
            with zipfile.ZipFile(temp_path,'w',zipfile.ZIP_DEFLATED) as bundle:
                add(bundle,'data/fastnews.db',read_file(snapshot))
                for folder_name in RESOURCES:
                    # Personal uploads follow this database; company resources follow the project.
                    base=(data if folder_name=='uploads' else root/'data')/folder_name
                    for file in sorted(base.rglob('*')):
                        if file.is_file() and not file.is_symlink() and file.resolve().is_relative_to(base.resolve()):
                            add(bundle,'data/'+folder_name+'/'+file.relative_to(base).as_posix(),read_file(file))
                for file in sorted(exports.rglob('*')):
                    if file.is_file() and not file.is_symlink() and file.resolve().is_relative_to(exports):
                        add(bundle,'exports/'+file.relative_to(exports).as_posix(),read_file(file))
                if drafts:
                    add(bundle,'data/restored-browser-drafts.json',json.dumps(browser_drafts(drafts),ensure_ascii=False).encode())
                bundle.writestr('BACKUP_MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        verify(temp_path)
        temp_path.replace(path)
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        return dict(name=path.name,path=str(path),size=path.stat().st_size,sha256=digest,counts=manifest['counts'],
                    browser_drafts_included=bool(drafts))
    finally:
        temp_path.unlink(missing_ok=True)


def allowed(name):
    p=PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or '\\' in name or ':' in name:return False
    if str(p)!=name or any(part.endswith((' ','.')) or re.search(r'[<>"|?*\x00-\x1f]',part) or
        re.fullmatch(r'(?i)(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',part) for part in p.parts):return False
    return name in {'data/fastnews.db','data/restored-browser-drafts.json'} or any(name.startswith('data/'+x+'/') for x in RESOURCES) or name.startswith('exports/')


def verify(archive):
    with zipfile.ZipFile(archive) as z:
        infos=z.infolist();names=[i.filename for i in infos]
        if len(names)!=len({n.casefold() for n in names}) or len(names)>10000 or sum(i.file_size for i in infos)>MAX_BYTES:
            raise ValueError('备份内容重复或超过容量上限')
        if 'BACKUP_MANIFEST.json' not in names:raise ValueError('不是本看板创建的数据备份')
        manifest=json.loads(z.read('BACKUP_MANIFEST.json'))
        if manifest.get('format')!='fastnews-user-backup-v1':raise ValueError('不支持此备份格式')
        files=manifest.get('files',[])
        if {x['path'] for x in files}!={n for n in names if n!='BACKUP_MANIFEST.json'} or len(files)!=len(names)-1:
            raise ValueError('备份文件清单不一致')
        if not any(x['path']=='data/fastnews.db' for x in files):raise ValueError('备份缺少数据库')
        for item in files:
            if not allowed(item['path']):raise ValueError('备份包含不允许恢复的路径')
            raw=z.read(item['path'])
            if len(raw)!=item['size'] or hashlib.sha256(raw).hexdigest()!=item['sha256']:
                raise ValueError('备份校验失败，文件可能不完整')
        if 'data/restored-browser-drafts.json' in names:browser_drafts(json.loads(z.read('data/restored-browser-drafts.json')))
    return manifest


def restore(archive, target):
    target=Path(target).resolve()
    if not (target/'tools/open_dashboard.py').is_file():raise ValueError('请选择已解压的新看板项目文件夹')
    if (target/'data/fastnews.db').exists() or (target/'data/dashboard-runtime.json').exists():
        raise ValueError('目标已有数据或曾启动。请另解压一份新看板后再恢复；原库不会覆盖')
    manifest=verify(archive)
    with tempfile.TemporaryDirectory(prefix='fastnews-restore-') as folder:
        staging=Path(folder)
        with zipfile.ZipFile(archive) as z:
            for item in manifest['files']:
                path=staging/item['path'];path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(z.read(item['path']))
        db=staging/'data/fastnews.db'
        from .db import connect
        with closing(sqlite3.connect(db)) as conn:
            if conn.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('数据库完整性不通过')
            row=conn.execute("SELECT value FROM fn_meta WHERE key='mode'").fetchone()
            mode=row[0] if row else 'live'
        # Validate migration ledger and compatibility before writing to the destination.
        with closing(connect(db,mode=mode)) as conn:
            if conn.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('恢复数据库存在引用缺口')
        # Historical exports and evidence paths are immutable provenance.
        # The download service locates the portable copy by its export identity.
        import shutil
        for item in manifest['files']:
            dest=target/item['path']
            if not dest.resolve().is_relative_to(target):raise ValueError('目标文件不属于项目目录')
            if dest.exists() and not item['path'].startswith(('data/writing_samples/','data/writing_templates/')):
                raise ValueError('目标含同名文件，请使用新解压的项目目录')
        for item in manifest['files']:
            if item['path']=='data/fastnews.db':continue
            dest=target/item['path'];dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(staging/item['path'],dest)
        (target/'data').mkdir(exist_ok=True)
        # Database is copied last: interrupted restores never present a partial DB.
        temp_db=target/'data/fastnews.db.restore-partial';shutil.copy2(db,temp_db);temp_db.replace(target/'data/fastnews.db')
        setup=target/'data/setup';setup.mkdir(exist_ok=True)
        (setup/'applied.json').write_text('{"applied":true,"restored":true}',encoding='utf-8')
        (target/'data/restore-report.json').write_text(json.dumps(dict(created_at=manifest['created_at'],counts=manifest['counts'],credentials_restored=False),ensure_ascii=False),encoding='utf-8')
    return dict(target=str(target),counts=manifest['counts'],credentials_restored=False)


def status(database, exports=None):
    data=Path(database).parent
    records=[]
    for path in sorted((data/'backups').glob('fastnews-backup-*.zip'),reverse=True)[:10]:
        records.append(dict(name=path.name,size=path.stat().st_size))
    files=list(data.rglob('*'))
    size=sum(p.stat().st_size for p in files if p.is_file() and not p.is_symlink())
    return dict(database=str(Path(database).resolve()),data_directory=str(data.resolve()),
        exports_directory=str(Path(exports or ROOT/'exports').resolve()),data_bytes=size,backups=records)


def recovery(database):
    restored=Path(database).parent/'restored-browser-drafts.json'
    drafts=browser_drafts(json.loads(restored.read_text(encoding='utf-8'))) if restored.exists() else {}
    return dict(restored_drafts=drafts,restore_id=hashlib.sha256(json.dumps(drafts,sort_keys=True).encode()).hexdigest() if drafts else None)
