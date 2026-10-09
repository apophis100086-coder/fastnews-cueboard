"""Prepare a source checkout without copying data or account credentials."""
from pathlib import Path
import argparse
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(args):
    subprocess.run([str(value) for value in args], cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='check only; do not install or change Git settings')
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        raise SystemExit('Python 3.11 or newer is required.')
    environment = ROOT / '.venv'
    python = environment / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    if not args.check:
        if not python.is_file():
            run([sys.executable, '-X', 'utf8', '-m', 'venv', environment])
        run([python, '-X', 'utf8', '-m', 'pip', 'install', '--disable-pip-version-check',
             '--progress-bar', 'off', '-r', ROOT / 'requirements.lock'])
    if not python.is_file():
        raise SystemExit('Project environment missing; run this script without --check first.')
    run([python, '-B', '-X', 'utf8', '-c',
         "import sys,yaml,tzdata,pypdf; assert sys.version_info >= (3,11); print('Python and dependencies: ready')"])
    run([python, '-B', '-X', 'utf8', '-m', 'fastnews', 'validate-config'])
    if not args.check:
        # Only configure the repository rooted here, never an enclosing project.
        try:
            root = subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], cwd=ROOT, text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            print('Source archive: no Git repository; hook setup skipped.')
        else:
            if Path(root).resolve() != ROOT.resolve():
                raise SystemExit('The Git root does not match this project; Git settings left unchanged.')
            run(['git', 'config', '--local', 'core.hooksPath', '.githooks'])
            print('Git pre-push checks: enabled (Node.js 20+ is required when pushing).')
    for library in ('writing_samples', 'writing_templates'):
        path = ROOT / 'data' / library / 'library.json'
        print(f'{library}: ' + ('present; validate contents before writing' if path.is_file() else 'missing; import authorized local resources before writing'))
    print('Setup does not start the service, import data, configure an API key, or verify a model account.')
    print('Start with the project launcher; see README.md for source-checkout instructions.')


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f'Setup stopped because a required command failed (exit {exc.returncode}).') from None
