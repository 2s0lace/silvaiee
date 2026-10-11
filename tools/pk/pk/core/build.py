"""Compile a staged working tree; publish only successful, journaled changes."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

from .errors import PkError
from .journal import Transaction, digest, snapshot
from .out import Result


def bash_path():
    configured = os.environ.get('PK_BASH')
    candidates = [configured] if configured else [r'C:\devkitPro\msys2\usr\bin\bash.exe', shutil.which('bash')]
    for value in candidates:
        if value and Path(value).is_file():
            return str(value)
    raise PkError('BUILD', 'bash missing; set PK_BASH to devkitPro MSYS2 bash.exe')


def errors(log):
    result = []
    for line in log.splitlines():
        if re.search(r'error:|fatal error:|undefined reference|\*\*\*|not found|No such file', line, re.I):
            line = ' '.join(line.split())[:300]
            if line not in result:
                result.append(line)
    return result[:15]


def verify_result(returncode, log, rom):
    exits = re.findall(r'^EXIT (\d+)\s*$', log, re.M)
    if returncode != 0 or not exits or exits[-1] != '0':
        raise PkError('BUILD', 'build_fr.sh failed; fix diagnostics and retry', errors(log) or ['missing successful EXIT marker'])
    if not rom.is_file() or rom.stat().st_size < 192:
        raise PkError('BUILD', f'{rom.name} missing or truncated; rebuild')
    with rom.open('rb') as stream:
        stream.seek(0xAC)
        if stream.read(4) != b'BPRE':
            raise PkError('BUILD', f'{rom.name} is not FireRed (BPRE); check build configuration')


# Not build inputs (Makefile/make_tools.mk never reference them): skipping them keeps staging
# smaller and lets pk itself and blueprints be edited while a build runs.
NOT_INPUTS = {'tools/pk', 'blueprints'}


def add_map_includes(text, names):
    """Every registered map needs `.include "data/maps/NAME/scripts.inc"` in
    data/event_scripts.s, else its `<NAME>_MapScripts` table never assembles and the
    build dies with an undefined reference. Idempotently appends the missing ones
    after the last existing map include."""
    text = text.replace('\r\n', '\n')
    missing = [n for n in names if f'.include "data/maps/{n}/scripts.inc"' not in text]
    if not missing:
        return text
    lines = text.splitlines(True)
    index = max((i for i, l in enumerate(lines) if 'scripts.inc"' in l and 'data/maps/' in l), default=-1)
    payload = ''.join(f'\t.include "data/maps/{n}/scripts.inc"\n' for n in missing)
    return ''.join(lines[:index + 1]) + payload + ''.join(lines[index + 1:])


def tree(root):
    result = []
    for directory, dirs, files in os.walk(root):
        here = Path(directory).relative_to(root).as_posix()
        dirs[:] = sorted(d for d in dirs if d not in ('.pk', '__pycache__')
                         and (d if here == '.' else f'{here}/{d}') not in NOT_INPUTS)
        for name in sorted(files):
            result.append((Path(directory)/name).relative_to(root).as_posix())
    return result


def run(repo, release=False, no_check=False, dry_run=False):
    if not no_check:
        repo.structural_check()
    target = ['release', 'BUILD=firered'] if release else ['firered']
    if dry_run:
        return Result('build dry-run ' + ' '.join(target))
    bash = bash_path()
    started = time.monotonic()
    state = repo.root/'.pk'
    state.mkdir(exist_ok=True)
    tx = Transaction(repo, 'build ' + ' '.join(target))
    original_paths = tree(repo.root)
    # TemporaryDirectory only removes this specific, freshly created staging directory.
    with tempfile.TemporaryDirectory(prefix='build-', dir=state) as tmp:
        stage = Path(tmp)
        tokens = {}
        for key in original_paths:
            source = repo.path(key)
            data, tokens[key] = snapshot(source)
            destination = stage/key
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            shutil.copystat(source, destination)
        includes = stage/'data/event_scripts.s'
        if includes.is_file():
            names = sorted(stage.joinpath('data/maps').glob('*/scripts.inc'))
            text = includes.read_text(encoding='utf-8')
            synced = add_map_includes(text, [p.parent.name for p in names])
            if synced != text:
                includes.write_bytes(synced.encode('utf-8'))   # published back to the repo by tx.stage below
        # Capture log from the same MSYS /tmp namespace; never trust the helper's exit status alone.
        # "$BASH" (not a PATH lookup): launched from Git Bash, PATH starts with Git's bash, whose
        # MSYS runtime lacks the /opt/devkitpro mount, and the build then cannot find the compiler.
        shell = ': > /tmp/fr_build.log; "$BASH" build_fr.sh "$@"; result=$?; cat /tmp/fr_build.log; exit "$result"'
        env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        env['PATH'] = str(Path(bash).parent) + os.pathsep + env.get('PATH', '')
        for key in ('MSYSTEM', 'SHELL', 'MAKE', 'MAKEFLAGS', 'MFLAGS'):
            env.pop(key, None)
        with (state/'build-output.log').open('wb') as output:
            process = subprocess.run([bash, '-c', shell, 'pk-build', *target], cwd=stage,
                                     stdout=output, stderr=subprocess.STDOUT, env=env)
        log = (state/'build-output.log').read_text(encoding='utf-8', errors='replace')
        rom_name = 'pokefirered-release.gba' if release else 'pokefirered.gba'
        verify_result(process.returncode, log, stage/rom_name)
        new_paths = tree(stage)
        # Verify the entire input snapshot, including deletions/additions during a long build.
        if tree(repo.root) != original_paths:
            raise PkError('CONFLICT', 'repository file list changed during build; retry')
        for key in original_paths:
            if snapshot(repo.path(key))[1] != tokens[key]:
                raise PkError('CONFLICT', f'{key} changed during build; retry')
        for key in sorted(set(original_paths) | set(new_paths)):
            data = (stage/key).read_bytes() if key in new_paths else None
            if digest(data) != (tokens[key][2] if key in tokens else None):
                tx.stage(key, data)
                if key in tokens and tx.tokens[key] != tokens[key]:
                    raise PkError('CONFLICT', f'{key} changed during build; retry')
        tx.commit()
        size = (stage/rom_name).stat().st_size / (1024*1024)
    return Result(f'build {round(time.monotonic()-started)}s {rom_name} {size:g}MB')
