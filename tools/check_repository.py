"""Check committed source in isolation before push; no provider/network calls."""
from __future__ import annotations

import argparse
import io
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BLOCKED_PARTS = {"data", "exports", "logs", "references", ".venv", "venv",
                 "node_modules", "__pycache__", ".codex"}
SECRET_PATTERNS = {
    "GitHub token": re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    "API token": re.compile(rb"sk-(?:proj-|ant-api\d+-)?[A-Za-z0-9_-]{32,}"),
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS access key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
}


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def require_clean(head: str) -> None:
    if git("status", "--porcelain") or git("rev-parse", "HEAD").decode().strip() != head:
        raise SystemExit("Commit or preserve outstanding changes first; the checked revision must match HEAD.")


def check_path(name: str) -> None:
    path=PurePosixPath(name)
    lower=path.name.lower()
    if path.is_absolute() or '..' in path.parts:
        raise SystemExit(f"Unsupported repository path: {name}")
    if (BLOCKED_PARTS.intersection(part.lower() for part in path.parts)
        or (lower.startswith('.env') and lower != '.env.example')
        or re.search(r'\.(?:db|sqlite3?)(?:-wal|-shm)?$|\.(?:pem|key|p12|pfx|log)$|\.zip(?:\.|$)',lower)):
        raise SystemExit(f"Private/runtime file is tracked in history: {name}")


def check_content(content: bytes, label: str) -> None:
    for kind,pattern in SECRET_PATTERNS.items():
        if pattern.search(content):
            # Print identifiers only, never the matching value.
            raise SystemExit(f"Possible {kind} in {label}; review locally.")


def push_tips(lines, head: str) -> list[str]:
    tips=[]
    for line in lines:
        fields=line.split()
        if len(fields)!=4:
            raise SystemExit('Invalid pre-push reference record.')
        local_ref,local_sha,remote_ref,_=fields
        if not local_sha.strip('0'):
            continue
        if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',local_sha):
            raise SystemExit('Invalid pre-push object ID.')
        if remote_ref.startswith('refs/heads/'):
            if local_sha!=head:
                raise SystemExit('Check out the branch being pushed, then push it separately.')
        elif remote_ref.startswith('refs/tags/'):
            # Restore tags may name an ancestor, but cannot bypass checks by
            # pointing at a different, untested line of development or a blob.
            commit=git('rev-parse',local_sha+'^{commit}').decode().strip()
            ancestor=subprocess.run(['git','merge-base','--is-ancestor',commit,head],cwd=ROOT,
                                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if ancestor.returncode:
                raise SystemExit('Tags must refer to the checked commit or its history.')
            target=local_sha
            while git('cat-file','-t',target).strip()==b'tag':
                annotation=git('cat-file','-p',target)
                check_content(annotation,'tag '+local_ref)
                target=annotation.splitlines()[0].decode().removeprefix('object ')
        else:
            raise SystemExit('Only branch and tag pushes are supported.')
        tips.append(local_sha)
    return tips


def check_history(tips: list[str]) -> int:
    """Deleted files still travel in Git history. Check every reachable version."""
    commits=git('rev-list',*tips).decode().splitlines()
    blobs={}
    for commit in commits:
        check_content(git('cat-file','-p',commit),'commit '+commit[:12])
        for entry in git('ls-tree','-r','-z',commit).split(b'\0'):
            if not entry:
                continue
            metadata,name=entry.split(b'\t',1)
            mode,kind,oid=metadata.decode().split()
            path=name.decode('utf-8')
            check_path(path)
            if kind!='blob' or mode not in {'100644','100755'}:
                raise SystemExit(f'Unsupported entry in history: {path}')
            blobs.setdefault(oid,path)
    # One Git process avoids thousands of subprocess launches on Windows.
    raw=subprocess.check_output(['git','cat-file','--batch'],cwd=ROOT,
                                input=('\n'.join(blobs)+'\n').encode()) if blobs else b''
    stream=io.BytesIO(raw)
    for oid,path in blobs.items():
        header=stream.readline().decode().split()
        if len(header)!=3 or header[:2]!=[oid,'blob']:
            raise SystemExit('Unable to verify a historical object.')
        content=stream.read(int(header[2]));stream.read(1)
        check_content(content,path+' (history)')
    return len(commits)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pre-push", action="store_true")
    args = parser.parse_args()
    head = git("rev-parse", "HEAD").decode().strip()
    tips=push_tips(sys.stdin,head) if args.pre_push else [head]
    require_clean(head)
    node = shutil.which("node")
    if node is None:
        raise SystemExit("Node.js 20+ is required for the frontend tests.")
    node_version=subprocess.check_output([node,'--version'],text=True).strip()
    if not re.fullmatch(r'v\d+\.\d+\.\d+',node_version) or int(node_version[1:].split('.')[0])<20:
        raise SystemExit('Node.js 20+ is required for the frontend tests.')
    history_count=check_history(list(dict.fromkeys([head,*tips])))
    archive = git("archive", "--format=tar", head)
    count = 0
    with tempfile.TemporaryDirectory(prefix="fastnews-git-check-") as directory:
        checkout = Path(directory)
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            for member in bundle:
                if member.isdir():
                    continue
                path = PurePosixPath(member.name)
                if not member.isfile() or path.is_absolute() or ".." in path.parts:
                    raise SystemExit(f"Unsupported archive entry: {member.name}")
                check_path(member.name)
                content = bundle.extractfile(member).read()
                check_content(content,member.name)
                destination = checkout.joinpath(*path.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
                count += 1
        print(f"Checking {head[:12]}: {count} files; {history_count} historical commits scanned.", flush=True)
        # Windows embedded Python's ._pth may include its original project.
        # Explicitly prioritize this checkout and verify the imported package.
        isolated = ('import sys, pathlib, unittest; '
                    'root=pathlib.Path.cwd().resolve(); sys.path.insert(0,str(root)); '
                    'import fastnews; '
                    'assert pathlib.Path(fastnews.__file__).resolve()==root/"fastnews"/"__init__.py", "Source checkout was not imported"; '
                    'unittest.main(module=None,argv=["unittest","discover","-s","tests","-q"])')
        subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', isolated], cwd=checkout, check=True)
        subprocess.run([node, "--test", "--test-reporter=dot", "tests/ui_daily_test.cjs"], cwd=checkout, check=True)
    require_clean(head)
    print("PASS: source-only checkout, Python and frontend tests. No live collection or LLM calls.")


if __name__ == "__main__":
    main()
