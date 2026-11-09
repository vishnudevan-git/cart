#!/usr/bin/env python3
"""
Back up this entire project into a single timestamped .zip.

    python backup.py --keep-running  # zip without stopping the server (default)
    python backup.py --stop-server   # stop matching project server first (Linux only)
    python backup.py -o /media/usb   # write the zip somewhere else
    python backup.py --list          # just list existing backups

The backup is a FULL copy meant for restoring, not a git export — so it
deliberately includes db.sqlite3, media/, logs/ and .env. Only regenerable
junk (__pycache__, *.pyc, venv/, .git/, staticfiles/) is left out.

Nothing is ever deleted: old backups are kept and simply listed at the end.

Stdlib only — no pip install, works on the dev laptop and on the server.
"""
import argparse
import os
import signal
import socket
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
PROJECT_NAME = PROJECT_DIR.name
DEFAULT_OUT_DIR = PROJECT_DIR.parent / f'{PROJECT_NAME}-backups'
SERVER_PORT = 8888

# Directories skipped wherever they appear in the tree.
SKIP_DIRS = {
    '__pycache__', '.git', '.hg', '.svn',
    'venv', 'env', '.venv', 'runvenv', 'winvenv', 'node_modules',
    '.mypy_cache', '.pytest_cache', '.ruff_cache', '.tox',
    'staticfiles', '.idea', '.vscode', '.kiro',
}
# Filename patterns skipped (matched with Path.match).
SKIP_FILE_PATTERNS = ('*.pyc', '*.pyo', '*.swp', '*.swo', '*~',
                      '.DS_Store', 'Thumbs.db', '*.sqlite3-journal')

# A running process is "our server" if its command line contains all the
# words of any one of these groups.
SERVER_SIGNATURES = (
    ('manage.py', 'runserver'),
    ('gunicorn', 'lakrish_mart'),
    ('daphne', 'lakrish_mart'),
    ('uvicorn', 'lakrish_mart'),
)


# --------------------------------------------------------------------------
# stopping the server
# --------------------------------------------------------------------------
def _iter_processes():
    """Yield (pid, cmdline, cwd) for every process we can read. Linux /proc."""
    proc = Path('/proc')
    if not proc.is_dir():
        return
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            raw = (entry / 'cmdline').read_bytes()
        except (OSError, PermissionError):
            continue
        cmdline = raw.replace(b'\0', b' ').decode('utf-8', 'replace').strip()
        if not cmdline:
            continue
        try:
            cwd = os.readlink(entry / 'cwd')
        except (OSError, PermissionError):
            cwd = ''
        yield pid, cmdline, cwd


def find_server_pids():
    """PIDs of dev/prod servers belonging to THIS project directory."""
    me = os.getpid()
    found = []
    for pid, cmdline, cwd in _iter_processes():
        if pid == me:
            continue
        if not any(all(word in cmdline for word in sig) for sig in SERVER_SIGNATURES):
            continue
        # Only ours: either it runs from this directory, or it names this path.
        if cwd == str(PROJECT_DIR) or str(PROJECT_DIR) in cmdline:
            found.append((pid, cmdline))
    return found


def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stop_server():
    """Ask every server process to quit, then insist. Returns what was stopped."""
    targets = find_server_pids()
    if not targets:
        if port_in_use(SERVER_PORT):
            print(f'  ! something is still listening on port {SERVER_PORT}, '
                  f'but it is not this project — leaving it alone')
        else:
            print('  server not running')
        return []

    for pid, cmdline in targets:
        print(f'  stopping pid {pid}: {cmdline[:70]}')
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError) as exc:
            print(f'    could not signal pid {pid}: {exc}')

    # Give them 5s to shut down cleanly, then force it.
    deadline = time.time() + 5
    while time.time() < deadline:
        if not any(_alive(pid) for pid, _ in targets):
            break
        time.sleep(0.2)

    for pid, _ in targets:
        if _alive(pid):
            print(f'    pid {pid} ignored SIGTERM — sending SIGKILL')
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    time.sleep(0.3)
    if port_in_use(SERVER_PORT):
        print(f'  ! port {SERVER_PORT} is still busy')
    else:
        print(f'  server stopped, port {SERVER_PORT} free')
    return targets


# --------------------------------------------------------------------------
# checkpointing sqlite so the copy inside the zip is consistent
# --------------------------------------------------------------------------
def checkpoint_sqlite():
    """Fold any -wal file back into db.sqlite3 so the zipped copy is complete."""
    db = PROJECT_DIR / 'db.sqlite3'
    if not db.exists():
        return
    try:
        import sqlite3
        conn = sqlite3.connect(str(db))
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE);')
        conn.close()
        print('  database checkpointed')
    except Exception as exc:            # a locked/absent db must not stop a backup
        print(f'  ! could not checkpoint database ({exc}) — zipping as-is')


# --------------------------------------------------------------------------
# zipping
# --------------------------------------------------------------------------
def should_skip_file(path: Path):
    return any(path.match(pattern) for pattern in SKIP_FILE_PATTERNS)


def collect_files(out_dir: Path):
    """Every file to archive, as (absolute path, path relative to project)."""
    files = []
    for root, dirnames, filenames in os.walk(PROJECT_DIR):
        root_path = Path(root)
        # Prune skipped directories and never descend into the backup folder.
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and (root_path / d).resolve() != out_dir
        ]
        dirnames.sort()
        for filename in sorted(filenames):
            full = root_path / filename
            if should_skip_file(full) or not full.is_file() or full.is_symlink():
                continue
            files.append((full, full.relative_to(PROJECT_DIR)))
    return files


def human(size):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024


def make_zip(out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y-%m-%d_%H%M%S')
    label = f'{PROJECT_NAME}_{stamp}'
    zip_path = out_dir / f'{label}.zip'

    files = collect_files(out_dir.resolve())
    raw_bytes = sum(f.stat().st_size for f, _ in files)
    print(f'  {len(files)} files, {human(raw_bytes)} uncompressed')

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for i, (full, rel) in enumerate(files, 1):
            # Everything lands under one folder, so unzipping never litters.
            zf.write(full, arcname=str(Path(label) / rel))
            if i % 250 == 0:
                print(f'    ...{i}/{len(files)}')
    return zip_path, len(files), raw_bytes


def verify(zip_path: Path, expected_count):
    with zipfile.ZipFile(zip_path) as zf:
        bad = zf.testzip()
        if bad is not None:
            return f'CORRUPT — first bad entry: {bad}'
        if len(zf.namelist()) != expected_count:
            return f'entry count mismatch: {len(zf.namelist())} != {expected_count}'
    return None


def list_backups(out_dir: Path):
    if not out_dir.is_dir():
        print(f'No backups yet in {out_dir}')
        return
    zips = sorted(out_dir.glob(f'{PROJECT_NAME}_*.zip'))
    if not zips:
        print(f'No backups yet in {out_dir}')
        return
    print(f'\nBackups in {out_dir}:')
    for path in zips:
        when = datetime.fromtimestamp(path.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
        print(f'  {path.name:<40} {human(path.stat().st_size):>10}   {when}')
    print(f'  ({len(zips)} total — nothing is auto-deleted)')


# --------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description='Zip this whole project for backup.')
    parser.add_argument('-o', '--out-dir', default=str(DEFAULT_OUT_DIR),
                        help=f'where to write the zip (default: {DEFAULT_OUT_DIR})')
    parser.add_argument('--stop-server', action='store_true',
                        help='stop this project server before zipping (explicit; Linux /proc only)')
    parser.add_argument('--keep-running', action='store_true',
                        help='legacy no-op; backups leave the server running by default')
    parser.add_argument('--list', action='store_true',
                        help='list existing backups and exit')
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()

    if args.list:
        list_backups(out_dir)
        return 0

    print(f'Backing up {PROJECT_DIR}')

    print('\n[1/4] Server')
    if args.stop_server:
        stop_server()
    else:
        print('  leaving the server running (default; use --stop-server for an offline backup)')

    print('\n[2/4] Database')
    checkpoint_sqlite()

    print('\n[3/4] Archiving')
    started = time.time()
    zip_path, count, raw_bytes = make_zip(out_dir)

    print('\n[4/4] Verifying')
    problem = verify(zip_path, count)
    if problem:
        print(f'  ! {problem}')
        print(f'\nFAILED — {zip_path} is not trustworthy. Kept for inspection.')
        return 1

    size = zip_path.stat().st_size
    saved = (1 - size / raw_bytes) * 100 if raw_bytes else 0
    print(f'  ok — {count} entries readable')
    print(f'\nDone in {time.time() - started:.1f}s')
    print(f'  {zip_path}')
    print(f'  {human(size)}  ({saved:.0f}% smaller than the {human(raw_bytes)} original)')

    list_backups(out_dir)
    if args.stop_server:
        print('\nRestart the server with:  venv\\Scripts\\python.exe manage.py runserver 0.0.0.0:8888')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\nInterrupted.')
        sys.exit(130)
