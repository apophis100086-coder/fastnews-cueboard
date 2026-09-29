"""Local Windows trial package: allowlisted source/resources, never business DBs."""
from pathlib import Path
import argparse,hashlib,json,subprocess,zipfile
ROOT=Path(__file__).resolve().parents[1]

def paths():
    tracked=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode('utf-8').split('\0')
    result={ROOT/n for n in tracked if n and not n.startswith(('.github/','tests/'))}
    for folder in ('runtime','data/writing_samples','data/writing_templates'):
        result.update(p for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in {'.pyc','.log'})
    result.add(ROOT/'data/setup/source-settings.json')
    for p in result:
        rel=p.relative_to(ROOT)
        if p.suffix in {'.db','.sqlite','.log','.key','.pem'} or '.git' in rel.parts or '.venv' in rel.parts:raise ValueError('Runtime/private record entered package: '+str(rel))
    return sorted(result)

def build(output):
    output.mkdir(parents=True,exist_ok=True)
    source=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip()
    if subprocess.check_output(['git','status','--porcelain'],cwd=ROOT).strip():raise ValueError('Commit the reviewed source before packaging')
    manifest=dict(format_version=1,source_commit=source,origin=json.loads((ROOT/'WORKBUDDY_ORIGIN.json').read_text(encoding='utf-8')),
        business_database_included=False,account_credentials_included=False,files=[])
    archive=output/'快讯AI看板-WorkBuddy-Windows试用包.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in paths():
            rel=p.relative_to(ROOT).as_posix();raw=p.read_bytes()
            manifest['files'].append(dict(path=rel,size=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
            z.writestr('快讯AI看板 WorkBuddy版/'+rel,raw)
        z.writestr('快讯AI看板 WorkBuddy版/PACKAGE_MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    skill=output/'fastnews-dashboard-skill.zip'
    base=ROOT/'.codebuddy/skills'
    with zipfile.ZipFile(skill,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for p in (base/'fastnews-dashboard').rglob('*'):
            if p.is_file():z.write(p,p.relative_to(base).as_posix())
    for file in (archive,skill):
        digest=hashlib.file_digest(file.open('rb'),'sha256').hexdigest()
        file.with_suffix(file.suffix+'.sha256').write_text(digest+'  '+file.name+'\n',encoding='utf-8')
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'source_commit':source,'files':len(manifest['files']),'archive':str(archive),'skill':str(skill),'size_bytes':archive.stat().st_size},ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT/'workbuddy-dist');args=p.parse_args();build(args.output)
