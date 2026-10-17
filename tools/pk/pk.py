#!/usr/bin/env python3
"""Thin CLI; every ordinary handler computes results and pending writes in memory."""
import importlib
from pathlib import Path
import sys
import traceback

# Explicit insertion also makes importlib-based tests independent of their cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pk.core.errors import PkError, CODES
from pk.core.out import Result, page
from pk.core.repo import Repo
from pk.core.journal import Transaction, undo, entries
from pk.index.builder import ensure, TABLES
from pk.index.query import find, refs
from pk.maps.view import listing, info
from pk.batch import plan
from pk.core.build import run as build
from pk.text.ops import command as text_command
from pk.maps import edit as map_edit, meta as map_meta, events as map_events, check as map_check
from pk.maps import resize as map_resize, create as map_create, terrain as map_terrain, lint as map_lint
from pk.maps import paintover as map_paintover, export as map_export
from pk.maps import polish as map_polish
from pk.art import ops as art_ops
from pk.bp import ops as bp_ops

MAP_LS_ALL = 100000    # pk map ls --all: no realistic repo has more than this many maps


def options(argv, values=(), flags=()):
    args, opts = [], {}
    i = 0
    while i < len(argv):
        word = argv[i]
        if word.startswith('--'):
            key = word[2:]
            if key in opts:
                raise PkError('PARSE', f'duplicate {word}')
            if key in flags:
                opts[key] = True
            elif key in values and i+1 < len(argv):
                i += 1
                opts[key] = argv[i]
            else:
                raise PkError('PARSE', f'unknown or incomplete {word}; use pk help')
        else:
            args.append(word)
        i += 1
    return args, opts


def pagination(opts, default=20):
    try:
        return int(opts.get('offset', 0)), int(opts.get('limit', default))
    except ValueError:
        raise PkError('PARSE', 'offset and limit must be integers') from None


def dispatch(repo, tx, argv):
    if not argv:
        argv = ['help']
    noun, *tail = argv
    if noun == 'help':
        if len(tail) > 1:
            raise PkError('PARSE', 'usage: pk help [noun]')
        if tail == ['errors']:
            result = Result('help errors', [f'{k} {v}' for k, v in CODES.items()])
        elif tail and (repo.root/f'tools/pk/help/{tail[0]}.md').is_file():
            # Per-noun detail lives in tools/pk/help/<noun>.md; AGENT.md stays a short index.
            result = Result(f'help {tail[0]}', repo.read(f'tools/pk/help/{tail[0]}.md').decode('utf-8').splitlines())
        elif not tail or tail[0] in ('find', 'refs', 'index', 'batch', 'build', 'undo', 'log', 'check'):
            result = Result('help', repo.read('tools/pk/AGENT.md').decode('utf-8').splitlines())
        else:
            raise PkError('NOTFOUND', f'help {tail[0]}; implemented nouns are in pk help')
    elif noun == 'index' and not tail:
        db = ensure(repo, tx, force=True)
        counts = ' '.join(f'{kind}:{db.execute("SELECT COUNT(*) FROM "+table).fetchone()[0]}' for kind, table in TABLES.items())
        db.close()
        result = Result('index', [counts])
    elif noun in ('find', 'refs'):
        args, opts = options(tail, values=('kind', 'offset', 'limit') if noun == 'find' else ('offset', 'limit'))
        if len(args) != 1:
            raise PkError('PARSE', f'usage: pk {noun} <query>')
        db = ensure(repo, tx)
        try:
            off, lim = pagination(opts, 18 if noun == 'refs' else 20)
            result = find(db, args[0], opts.get('kind'), off, lim) if noun == 'find' else refs(repo, db, args[0], off, lim)
        finally:
            db.close()
    elif noun == 'map':
        args, opts = options(tail, values=('offset', 'limit', 'rect', 'layer', 'from', 'coll', 'elev', 'add', 'to',
                                           'anchor', 'fill', 'size', 'tilesets', 'type', 'like', 'music', 'group', 'out', 'scale',
                                           'into', 'viewport', 'compare', 'ring', 'max-steps'),
                             flags=('exact', 'no-events', 'fork-layout', 'drop-out-of-bounds', 'stamps', 'snapshot',
                                    'all', 'grid', 'repair', 'annotate'))
        if args and args[0] == 'paintover' and len(args) == 3:
            result = map_paintover.command(repo, tx, args, opts)
        elif args and args[0] == 'polish' and len(args) == 2:
            result = map_polish.command(repo, tx, args[1], opts)
        elif args and args[0] == 'resize' and len(args) == 2:
            result = map_resize.run(repo, tx, args[1], opts)
        elif args and args[0] == 'extend-to' and len(args) == 3:
            result = map_resize.extend_to(repo, tx, args[1], args[2], opts)
        elif args and args[0] == 'new' and len(args) == 2:
            result = map_create.new_map(repo, tx, args[1], opts)
        elif args and args[0] == 'delete' and len(args) == 2:
            result = map_create.delete_map(repo, tx, args[1])
        elif args and args[0] == 'ls' and len(args) <= 2:
            offset, limit = pagination(opts)
            if opts.get('all'):
                offset, limit = 0, MAP_LS_ALL
            result = listing(repo, args[1] if len(args) == 2 else '', offset, limit)
        elif args and args[0] == 'lint':
            if opts.get('snapshot') and len(args) == 1:
                result = map_lint.build_snapshot(repo, tx)
            elif len(args) == 2:
                result = map_lint.run(repo, args[1], show_all=opts.get('all', False))
            else:
                raise PkError('PARSE', 'usage: pk map lint MAP [--all] | pk map lint --snapshot')
        elif args and args[0] == 'export' and len(args) == 2:
            result = map_export.export_map(repo, tx, args[1], opts)
        elif args and args[0] == 'import' and len(args) == 3:
            result = map_export.import_map(repo, tx, args[1], args[2], opts)
        elif len(args) == 2 and args[0] == 'info':
            result = info(repo, args[1], *pagination(opts, 6))
        elif args:
            if args[0] == 'grid' and len(args) == 3:
                opts['stdin'] = sys.stdin.read()
            result = map_edit.command(repo, tx, args, opts)
        else:
            raise PkError('PARSE', 'usage: pk map ls|info|show|at|lint|paint|fill|line|grid|copy|border|paintover|resize|extend-to|new')
    elif noun == 'meta':
        args, opts = options(tail, values=('offset', 'limit', 'tags', 'sym', 'png'), flags=('unnamed', 'overwrite'))
        if not args:
            raise PkError('PARSE', 'usage: pk meta ls|name|autoname|show')
        result = map_meta.command(repo, tx, args, opts, pagination)
    elif noun == 'terrain':
        args, opts = options(tail, values=('offset', 'limit', 'rect', 'dir', 'stamp', 'diag', 'options', 'pick'))
        if not args:
            raise PkError('PARSE', 'usage: pk terrain learn|show|brush|river|ledge|forest|bake')
        result = map_terrain.command(repo, tx, args, opts, pagination)
    elif noun == 'bp':
        args, opts = options(tail, values=('offset', 'limit', 'status', 'section', 'out', 'region', 'rect', 'scale'),
                             flags=('overwrite', 'strict'))
        if not args:
            raise PkError('PARSE', 'usage: pk bp world|show|plan|check|apply|diff|render|export')
        result = bp_ops.command(repo, tx, args, opts, pagination)
    elif noun == 'tileset':
        args, opts = options(tail, values=('which', 'out', 'scale'))
        if not args:
            raise PkError('PARSE', 'usage: pk tileset build|lint|preview|export')
        result = art_ops.tileset_command(repo, tx, args, opts)
    elif noun == 'px':
        args, opts = options(tail, values=('out', 'scale', 'size', 'grid', 'palette', 'colors', 'key', 'layer', 'crop', 'tolerance'))
        if not args:
            raise PkError('PARSE', 'usage: pk px show|check|render FILE.px')
        result = art_ops.px_command(repo, tx, args, opts)
    elif noun == 'sprite':
        args, opts = options(tail, values=('like',))
        from pk.art import sprite as art_sprite
        result = art_sprite.command(repo, tx, args, opts)
    elif noun == 'stamp':
        args, opts = options(tail, values=('offset', 'limit', 'walk', 'on', 'min-size', 'min-count', 'cap'),
                             flags=('overwrite', 'apply'))
        if not args:
            raise PkError('PARSE', 'usage: pk stamp extract|from-tileset|mine|ls|place')
        result = map_create.stamp(repo, tx, args, opts, pagination)
    elif noun == 'ev':
        args, opts = options(tail, values=('offset', 'limit'))
        if not args:
            raise PkError('PARSE', 'usage: pk ev ls|add|set|rm <map> ...')
        result = map_events.command(repo, tx, args, opts, pagination)
    elif noun == 'text':
        args, opts = options(tail, values=('offset', 'limit', 'grep', 'scope'), flags=('keep-breaks', 'estimates'))
        result = text_command(repo, tx, args, opts, pagination)
    elif noun == 'log':
        args, opts = options(tail, values=('offset', 'limit'))
        if args:
            raise PkError('PARSE', 'usage: pk log [--offset n --limit n]')
        rows = [f"{n} {m['time']} {m['command']}" for n, m in reversed(entries(repo.root)) if m['state'] == 'committed']
        result = page(f'log {len(rows)}', rows, *pagination(opts))
    elif noun == 'check':
        args, opts = options(tail, values=('offset', 'limit'), flags=('warnings', 'known', 'baseline'))
        if len(args) > 1:
            raise PkError('PARSE', 'usage: pk check [map|all] [--warnings]')
        result = map_check.run(repo, tx, args[0] if args else 'all', opts, pagination)
    elif noun == 'kit':
        args, opts = options(tail, values=('size', 'top', 'bottom', 'left', 'right', 'feature', 'pair', 'limit'),
                             flags=('sheet',))
        if not args:
            raise PkError('PARSE', 'usage: pk kit index | find [filters] | fit MAP x,y,WxH')
        from pk.maps import kit as map_kit
        result = map_kit.command(repo, args, opts)
    else:
        raise PkError('PARSE', f'unsupported command {noun}; use pk help')
    return result, dict(tx.writes)


def main(argv=None, stdin=None, root=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = '--json' in argv
    dry_run = '--dry-run' in argv
    argv = [a for a in argv if a not in ('--json', '--dry-run')]
    repo = None
    try:
        repo = Repo(root) if root else Repo.discover()
        tx = Transaction(repo, ' '.join(argv) or 'help')
        state = Repo(repo.root, tx)
        if argv and argv[0] == 'build':
            args, opts = options(argv[1:], flags=('release', 'no-check'))
            if args:
                raise PkError('PARSE', 'usage: pk build [--release] [--no-check]')
            result = build(repo, opts.get('release', False), opts.get('no-check', False), dry_run)
        elif argv and argv[0] == 'undo':
            if len(argv) > 2 or (len(argv) == 2 and not argv[1].isdigit()):
                raise PkError('PARSE', 'usage: pk undo [journal-number]')
            number, _ = undo(repo, int(argv[1]) if len(argv) == 2 else None, dry_run)
            result = Result(f'undo {number}' + (' dry-run' if dry_run else ''))
        else:
            if argv and argv[0] == 'batch':
                if len(argv) != 1:
                    raise PkError('PARSE', 'usage: pk batch < ops.txt')
                result = plan(state, tx, sys.stdin.read() if stdin is None else stdin, dispatch)
            else:
                result, _ = dispatch(state, tx, argv)
            tx.commit(dry_run)
            if dry_run:
                result.summary += ' dry-run'
    except PkError as exc:
        result = Result(str(exc), exc.data, ok=False, code=exc.code)
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        result = Result(f'invalid source or argument: {exc}; repair input', ok=False, code='PARSE')
    except Exception:
        if repo:
            state_dir = repo.root/'.pk'
            state_dir.mkdir(exist_ok=True)
            (state_dir/'error.log').write_text(traceback.format_exc(), encoding='utf-8')
        result = Result('unexpected failure; inspect .pk/error.log', ok=False, code='INTERNAL')
    return result.render(as_json), 0 if result.ok else 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    sys.stderr.reconfigure(encoding='utf-8', newline='\n')
    output, status = main()
    print(output)
    sys.exit(status)
