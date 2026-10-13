"""Hash a prepared portable bundle without including machine-specific state."""
import argparse
import hashlib
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--bundle', type=Path, required=True)
parser.add_argument('--revision', required=True)
args = parser.parse_args()
root = args.bundle.resolve()
paths = []
for name in ('app', 'frontend', 'runtime'):
    directory = root / name
    if not directory.is_dir():
        raise SystemExit(f'Missing directory: {name}')
    paths.extend(path for path in directory.rglob('*') if path.is_file())
for name in ('lan_server.py','service_entry.py','windows_limits.py','initialize_lan.py',
             'install-lan.ps1','grant-batch-logon.ps1','show-access-code.ps1',
             'rotate-access-code.ps1','stop-lan.ps1','README.md','LICENSE','THIRD-PARTY-LICENSES.txt'):
    path = root / name
    if not path.is_file():
        raise SystemExit(f'Missing file: {name}')
    paths.append(path)
files = []
for path in sorted(paths):
    if not path.resolve().is_relative_to(root):
        raise SystemExit(f'External link refused: {path}')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    files.append({'path': path.relative_to(root).as_posix(), 'sha256': digest.hexdigest()})
(root / 'bundle-manifest.json').write_text(json.dumps({'source_revision': args.revision,
    'files': files}, indent=2), encoding='utf-8')
print(f'Hashed {len(files)} files; private data/configuration excluded.')
