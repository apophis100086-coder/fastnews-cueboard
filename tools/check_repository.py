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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pre-push", action="store_true")
    args = parser.parse_args()
    head = git("rev-parse", "HEAD").decode().strip()
    if args.pre_push:
        for line in sys.stdin:
            local_ref, local_sha, _, _ = line.split()
            # Historical tags are restore points. Branch pushes must be the checked HEAD.
            if local_sha.strip("0") and local_ref.startswith("refs/heads/") and local_sha != head:
                raise SystemExit("Check out the branch being pushed, then push it separately.")
    require_clean(head)
    node = shutil.which("node")
    if node is None:
        raise SystemExit("Node.js 20+ is required for the frontend tests.")
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
                name = path.name.lower()
                if (BLOCKED_PARTS.intersection(part.lower() for part in path.parts)
                    or (name.startswith(".env") and name != ".env.example")
                    or re.search(r"\.(?:db|sqlite3?)(?:-wal|-shm)?$|\.(?:pem|key|p12|pfx|log)$|\.zip(?:\.|$)", name)):
                    raise SystemExit(f"Private/runtime file is tracked: {member.name}")
                content = bundle.extractfile(member).read()
                for label, pattern in SECRET_PATTERNS.items():
                    if pattern.search(content):
                        # Never print the matched value.
                        raise SystemExit(f"Possible {label} in {member.name}; review locally.")
                destination = checkout.joinpath(*path.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
                count += 1
        print(f"Checking {head[:12]}: {count} tracked files; private runtime files excluded.", flush=True)
        subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"], cwd=checkout, check=True)
        subprocess.run([node, "--test", "--test-reporter=dot", "tests/ui_daily_test.cjs"], cwd=checkout, check=True)
    require_clean(head)
    print("PASS: source-only checkout, Python and frontend tests. No live collection or LLM calls.")


if __name__ == "__main__":
    main()
