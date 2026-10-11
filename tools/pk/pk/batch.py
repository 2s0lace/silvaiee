import shlex
from .core.errors import PkError
from .core.out import Result


def plan(repo, tx, source, dispatch):
    commands = []
    for n, line in enumerate(source.splitlines(), 1):
        try:
            words = shlex.split(line, comments=True, posix=True)
        except ValueError as exc:
            raise PkError('PARSE', f'batch line {n}: {exc}') from None
        if words and words[0] == 'pk':
            words.pop(0)
        if not words:
            continue
        if words[0] in ('batch', 'build', 'undo') or any(x in words for x in ('--dry-run', '--json')):
            raise PkError('PARSE', f'batch line {n}: build/undo/nested batch and per-line output flags unsupported; run separately')
        commands.append((n, words))
    if len(commands) > 100:
        raise PkError('LIMIT', 'batch supports at most 100 operations; split the batch')
    rows = []
    for n, words in commands:
        try:
            result, _ = dispatch(repo, tx, words)
            rows.append(result.summary)
        except PkError as exc:
            raise PkError(exc.code, f'batch line {n}: {exc}', exc.data) from None
    return Result(f'batch {len(commands)} operations {len(tx.writes)} files', rows[:18],
                  more=18 if len(rows) > 18 else None)
