"""Durable multi-file journal; optimistic concurrency and recoverable failures.

Only this module installs writes. Atomic rename is per file; prepared manifests
make an interrupted multi-file install detectable and recoverable with undo.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .errors import PkError
from .repo import Repo


def digest(data):
    return None if data is None else hashlib.sha256(data).hexdigest()


def snapshot(path):
    try:
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            raise PkError('CONFLICT', f'{path.name} changed while reading; retry')
        return data, (after.st_mtime_ns, len(data), digest(data))
    except FileNotFoundError:
        return None, None


def atomic_write(path, data):
    if data is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.pk-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def save_manifest(folder, manifest):
    atomic_write(folder / 'manifest.json', (json.dumps(manifest, sort_keys=True, indent=2)+'\n').encode())


@contextmanager
def locked(root):
    state = root / '.pk'
    state.mkdir(exist_ok=True)
    with (state / 'lock').open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise PkError('CONFLICT', 'another pk transaction is running; retry when it finishes') from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def entries(root):
    journal = root / '.pk/journal'
    result = []
    if journal.exists():
        for folder in journal.iterdir():
            if folder.name.isdigit() and (folder/'manifest.json').is_file():
                try:
                    result.append((int(folder.name), json.loads((folder/'manifest.json').read_bytes())))
                except (ValueError, OSError):
                    raise PkError('CONFLICT', f'journal {folder.name} unreadable; restore its manifest') from None
    return sorted(result)


class Transaction:
    def __init__(self, repo, command):
        self.repo = Repo(repo.root)
        self.command = command
        self.original = {}
        self.tokens = {}
        self.writes = {}

    def capture(self, relative):
        path = self.repo.path(relative)
        key = path.relative_to(self.repo.root).as_posix()
        if key.startswith('.pk/journal/') or key == '.pk/lock':
            raise PkError('PARSE', 'journal internals cannot be transaction targets')
        if key not in self.original:
            self.original[key], self.tokens[key] = snapshot(path)
        return key

    def read(self, relative):
        key = self.capture(relative)
        value = self.writes.get(key, self.original[key])
        if value is None:
            raise PkError('NOTFOUND', f'{key}; restore missing source')
        return value

    def stage(self, relative, data):
        if data is not None and not isinstance(data, bytes):
            raise TypeError('writes must be bytes or None')
        key = self.capture(relative)
        self.writes[key] = data

    def verify(self):
        for key, token in self.tokens.items():
            if snapshot(self.repo.path(key))[1] != token:
                raise PkError('CONFLICT', f'{key} changed since read; reread and retry')

    def commit(self, dry_run=False, allow_pending=None):
        changed = {k: v for k, v in self.writes.items() if v != self.original[k]}
        self.verify()
        if not changed or dry_run:
            return None
        with locked(self.repo.root):
            old_entries = entries(self.repo.root)
            pending = [n for n, m in old_entries if m['state'] in ('prepared', 'recovery_required') and n != allow_pending]
            if pending:
                raise PkError('CONFLICT', f'incomplete journal {pending[0]}; run pk undo {pending[0]}')
            self.verify()
            journal = self.repo.root / '.pk/journal'
            journal.mkdir(exist_ok=True)
            n = max([int(p.name) for p in journal.iterdir() if p.name.isdigit()], default=0)+1
            folder = journal / str(n)
            folder.mkdir()
            manifest = dict(version=1, state='prepared', command=self.command,
                            time=datetime.now(timezone.utc).isoformat(timespec='seconds'), files=[])
            for i, key in enumerate(sorted(changed)):
                old = self.original[key]
                backup = f'{i}.before' if old is not None else None
                if backup:
                    atomic_write(folder/backup, old)
                manifest['files'].append(dict(path=key, backup=backup, before=digest(old), after=digest(changed[key])))
            save_manifest(folder, manifest)
            installed = []
            try:
                self.verify()
                for entry in manifest['files']:
                    key = entry['path']
                    if snapshot(self.repo.path(key))[1] != self.tokens[key]:
                        raise PkError('CONFLICT', f'{key} changed during commit; retry')
                    atomic_write(self.repo.path(key), changed[key])
                    installed.append(key)
                manifest['state'] = 'committed'
                save_manifest(folder, manifest)
            except BaseException:
                failed = []
                for key in reversed(installed):
                    try:
                        if digest(snapshot(self.repo.path(key))[0]) != digest(changed[key]):
                            failed.append(key)
                            continue
                        atomic_write(self.repo.path(key), self.original[key])
                    except OSError:
                        failed.append(key)
                manifest['state'] = 'recovery_required' if failed else 'rolled_back'
                save_manifest(folder, manifest)
                if failed:
                    raise PkError('CONFLICT', f'rollback incomplete; inspect journal {n}, then pk undo {n}') from None
                raise
            return n


def undo(repo, number=None, dry_run=False):
    available = [(n, m) for n, m in entries(repo.root) if m['state'] in ('committed', 'prepared', 'recovery_required')]
    if number is None:
        if not available:
            raise PkError('NOTFOUND', 'no transaction to undo')
        number, manifest = available[-1]
    else:
        candidates = [m for n, m in available if n == number]
        if not candidates:
            raise PkError('NOTFOUND', f'journal {number}; use pk log')
        manifest = candidates[0]
    tx = Transaction(repo, f'undo {number}')
    for entry in manifest['files']:
        if entry['path'].startswith('.pk/'):
            # Caches (index.sqlite) and renders are rebuilt on demand; restoring an old cache
            # would only block undo whenever the index has been refreshed since.
            continue
        key = tx.capture(entry['path'])
        current = digest(tx.original[key])
        allowed = [entry['after']]
        if manifest['state'] != 'committed':
            allowed.append(entry['before'])
        if current not in allowed:
            raise PkError('CONFLICT', f'{key} changed after journal {number}; preserve external edits first')
        old = (repo.root/'.pk/journal'/str(number)/entry['backup']).read_bytes() if entry['backup'] else None
        if digest(old) != entry['before']:
            raise PkError('CONFLICT', f'journal {number} backup damaged; restore backup')
        tx.stage(key, old)
    result = tx.commit(dry_run, allow_pending=number)
    if not dry_run and manifest['state'] != 'committed':
        manifest['state'] = 'rolled_back'
        with locked(repo.root):
            save_manifest(repo.root/'.pk/journal'/str(number), manifest)
    return number, result
