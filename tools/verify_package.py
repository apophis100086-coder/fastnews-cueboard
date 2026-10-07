"""Verify a mentor ZIP without running it or trusting its extraction paths."""
import argparse,hashlib,json,zipfile
from pathlib import Path,PurePosixPath


def verify(path):
    with zipfile.ZipFile(path) as z:
        names=z.namelist()
        if len(names)!=len({n.casefold() for n in names}):raise ValueError('Duplicate names')
        for name in names:
            p=PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or '\\' in name or ':' in name:raise ValueError('Unsafe path')
        roots={n.split('/')[0] for n in names}
        if len(roots)!=1:raise ValueError('Expected one project folder')
        root=roots.pop()+'/'
        manifest=json.loads(z.read(root+'PACKAGE_MANIFEST.json'))
        expected={root+x['path'] for x in manifest['files']}|{root+'PACKAGE_MANIFEST.json'}
        if expected!=set(names):raise ValueError('File list mismatch')
        for item in manifest['files']:
            raw=z.read(root+item['path'])
            if len(raw)!=item['size'] or hashlib.sha256(raw).hexdigest()!=item['sha256']:raise ValueError('Checksum mismatch: '+item['path'])
            p=PurePosixPath(item['path'])
            if p.suffix in {'.db','.sqlite','.key','.pem','.log'} or '.git' in p.parts:raise ValueError('Private record present')
            if p.parts[0]=='data' and p.parts[1] not in {'writing_samples','writing_templates','setup'}:raise ValueError('Unexpected runtime data')
        for required in ['runtime/python/python.exe','runtime/python/pythonw.exe','fastnews/static/index.html',
                '.agents/skills/fastnews-dashboard/SKILL.md','.codebuddy/skills/fastnews-dashboard/SKILL.md',
                '一键启动.cmd','恢复数据.cmd','使用指南.html']:
            if root+required not in names:raise ValueError('Missing '+required)
    return dict(verified=True,files=len(manifest['files']),source_commit=manifest['source_commit'],
                archive_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest())


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('archive');args=p.parse_args()
    print(json.dumps(verify(args.archive),ensure_ascii=False,indent=2))
