"""Portable mentor package; shared API credential only through explicit opt-in."""
from pathlib import Path
import argparse,hashlib,json,subprocess,zipfile
ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(ROOT))
from fastnews.workspace import settings
PACKAGE_FOLDER='快讯AI看板 带教版'
PACKAGE_PORT=8797

def paths():
    tracked=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode('utf-8').split('\0')
    result={ROOT/n for n in tracked if n and not n.startswith(('.github/','tests/'))}
    for folder in ('runtime','data/writing_samples','data/writing_templates'):
        result.update(p for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in {'.pyc','.log'})
    for p in result:
        rel=p.relative_to(ROOT)
        if p.is_symlink() or not p.resolve().is_relative_to(ROOT) or p.suffix in {'.db','.sqlite','.log','.key','.pem'} or '.git' in rel.parts or '.venv' in rel.parts:raise ValueError('Runtime/private record entered package: '+str(rel))
        if rel.parts[0]=='data' and rel.parts[1] not in {'writing_samples','writing_templates'}:raise ValueError('Unexpected data file')
    return sorted(result)

def build(output,shared_api_file=None):
    from tools.verify_package import verify_resources
    resources=verify_resources(lambda name:(ROOT/name).read_bytes())
    for name in ('runtime/python/python.exe','runtime/python/pythonw.exe'):
        if not (ROOT/name).is_file():raise ValueError('Missing bundled runtime: '+name)
    output.mkdir(parents=True,exist_ok=True)
    source=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip()
    if subprocess.check_output(['git','status','--porcelain'],cwd=ROOT).strip():raise ValueError('Commit the reviewed source before packaging')
    manifest=dict(format_version=2,source_commit=source,origin=json.loads((ROOT/'WORKBUDDY_ORIGIN.json').read_text(encoding='utf-8')),
        entries=['workbuddy','api','codex'],default_entry='workbuddy',port=PACKAGE_PORT,
        business_database_included=False,account_credentials_included=False,resources=resources,files=[])
    archive=output/'快讯AI看板-Windows带教包.zip'
    folder=PACKAGE_FOLDER
    seed=json.loads((ROOT/'data/setup/source-settings.json').read_text(encoding='utf-8'))
    # Carry the reviewed default source preferences, not personal profile names or history.
    seed['profiles']=[dict(profile_id='default',name='默认方案',settings_json=json.dumps(dict(host='workbuddy',web_search=True,agent_direct=True)),created_at='2026-10-08T00:00:00+08:00')]
    seed['preferences']=[v for v in seed.get('preferences',[]) if v.get('profile_id')=='default']
    overrides={'config/workspace.yaml':f'version: mentor-workspace-v1\nport: {PACKAGE_PORT}\nagent_direct: true\npackage_folder: {folder}\n'.encode(),
               'data/setup/source-settings.json':json.dumps(seed,ensure_ascii=False,indent=2).encode()}
    if shared_api_file:
        from fastnews.shared_api import read_seed,SEED,NOTICE
        shared=read_seed(shared_api_file)
        overrides[SEED]=json.dumps(shared,ensure_ascii=False,indent=2).encode()
        overrides['共享API说明.txt']=(NOTICE+'\n\n默认入口仍是 WorkBuddy。切换到自接 API 后，无需输入密钥；确认材料发送和费用授权，保存 API 配置、保存入口，即可检查并使用。\n模型：'+shared['model']+'\n服务：https://api.deepseek.com\n').encode('utf-8')
        manifest.update(account_credentials_included=True,shared_api_included=True,shared_api_provider='DeepSeek',
                        shared_api_model=shared['model'],shared_key_storage='portable_plaintext_seed_and_windows_local_encryption')
        archive=output/'快讯AI看板-Windows带教包-DeepSeek共享版.zip'
    originals={p.relative_to(ROOT).as_posix():p for p in paths()}
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for rel in sorted(set(originals)|set(overrides)):
            raw=overrides[rel] if rel in overrides else originals[rel].read_bytes()
            manifest['files'].append(dict(path=rel,size=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
            z.writestr(folder+'/'+rel,raw)
        z.writestr(folder+'/PACKAGE_MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    # A single project ZIP is the deliverable; detached skills cannot locate its runtime.
    for file in (archive,):
        digest=hashlib.file_digest(file.open('rb'),'sha256').hexdigest()
        file.with_suffix(file.suffix+'.sha256').write_text(digest+'  '+file.name+'\n',encoding='utf-8')
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'source_commit':source,'files':len(manifest['files']),'archive':str(archive),'size_bytes':archive.stat().st_size},ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT/'workbuddy-dist')
    p.add_argument('--shared-api-file',type=Path,help='Explicitly bundle this authorized local DeepSeek key file (never tracked)')
    args=p.parse_args();build(args.output,args.shared_api_file)
