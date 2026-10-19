"""Chunk grids, bounded merging, held-out evidence, and measurable seams."""
from pathlib import Path
import random
import json
import importlib.util
import sys
import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pk.maps import blockout, chunks
from pk.core.errors import PkError


def entry(rows, layout='LAYOUT_TEST'):
    return dict(layout=layout, rows=rows, elevation=['3' * len(row) for row in rows], stratum='route')


class Chunks(unittest.TestCase):
    def test_two_grids_do_not_join_or_invent_partial_cells(self):
        rows = blockout.Rows(['.' * 9] * 9)
        rows.elevation = ['3' * 9] * 9
        parts = list(chunks.cut(rows))
        self.assertEqual([(o, x, y) for o, x, y, _, _ in parts],
                         [(0, 0, 0), (0, 4, 0), (0, 0, 4), (0, 4, 4), (2, 2, 2)])
        self.assertTrue(all(e == '3' * 16 for _, _, _, _, e in parts))

    def test_bit_distance_equals_letter_distance(self):
        rng = random.Random(91)
        alphabet = sorted(blockout.LETTERS)
        for _ in range(100):
            a = ''.join(rng.choices(alphabet, k=16))
            b = ''.join(rng.choices(alphabet, k=16))
            self.assertEqual(chunks.distance(chunks.code(a), chunks.code(b)), sum(x != y for x, y in zip(a, b)))

    def test_exact_deduplication_and_near_merge_have_provenance(self):
        corpus = {'A': entry(['....'] * 4, 'A'), 'B': entry(['g...'] + ['....'] * 3, 'B')}
        v = chunks.Vocabulary(corpus)
        self.assertEqual(len(v.kinds), 1)
        self.assertEqual(v.kinds[0]['frequency'], 2)
        self.assertEqual({s['map'] for s in v.kinds[0]['sources']}, {'A', 'B'})
        self.assertEqual(chunks.score(v, corpus['B']['rows'])['chunk_fit'], 1)

    def test_merge_is_not_a_transitive_chain(self):
        corpus = {str(i): entry([s[:4], s[4:8], s[8:12], s[12:]], str(i))
                  for i, s in enumerate(['.' * 16, 'gg' + '.' * 14, 'gggg' + '.' * 12])}
        v = chunks.Vocabulary(corpus)
        self.assertEqual(len(v.kinds), 2)
        for letters, k in v.exact.items():
            self.assertLessEqual(chunks.distance(chunks.code(letters), v.kinds[k]['code']), 2)

    def test_layout_holdout_drops_aliases_and_pk_never_trains(self):
        corpus = {'A': entry(['....'] * 4, 'SHARED'), 'Alias': entry(['....'] * 4, 'SHARED'),
                  'B': entry(['TTTT'] * 4, 'B'), 'PkFake': entry(['ssss'] * 4, 'P')}
        v = chunks.Vocabulary(corpus, ('A',))
        self.assertEqual(v.excluded, ['A', 'Alias'])
        self.assertEqual({s['map'] for k in v.kinds for s in k['sources']}, {'B'})
        self.assertEqual(chunks.score(v, corpus['A']['rows'])['chunk_fit'], 0)

    def test_pk_alias_of_vanilla_layout_still_holds_out_layout(self):
        corpus = {'A': entry(['....'] * 4, 'SHARED'), 'B': entry(['TTTT'] * 4, 'B')}
        grid = SimpleNamespace(name='PkCopy', data={'layout': 'SHARED'})
        self.assertEqual(chunks.holdout(corpus, grid), ('A',))

    def test_nearest_is_global_even_without_near_signature(self):
        v = chunks.Vocabulary({'A': entry(['....'] * 4), 'B': entry(['TTTT'] * 4, 'B')})
        d, k = v.nearest('ggg.' * 4)
        self.assertEqual(d, 12)
        self.assertEqual(v.kinds[k]['letters'], '.' * 16)

    def test_adjacency_is_directional_and_seams_use_facing_edges(self):
        v = chunks.Vocabulary({'A': entry(['....TTTT'] * 4)})
        a = v.exact['.' * 16]
        b = v.exact['T' * 16]
        self.assertEqual(v.adjacency[a, b, 'E'], 1)
        self.assertEqual(v.adjacency[b, a, 'W'], 1)
        self.assertFalse(v.seam(a, b, 'S'))
        self.assertTrue(v.seam(a, b, 'E'))
        self.assertTrue(v.seam(a, a, 'N'))  # unseen pair, equal edge letters
        self.assertEqual(chunks.score(v, ['....TTTT'] * 4)['seam_fit'], 1)

    def test_no_pairs_is_unmeasured_and_small_maps_are_rejected(self):
        v = chunks.Vocabulary({'A': entry(['....'] * 4)})
        self.assertIsNone(chunks.score(v, ['....'] * 4)['seam_fit'])
        with self.assertRaises(PkError):
            chunks.score(v, ['...'] * 3)

    def test_percentiles_use_per_map_linear_interpolation(self):
        self.assertEqual(chunks.percentile([0, 1], .5), .5)
        self.assertEqual(chunks.percentile([0, 1], .05), .05)

    def test_median_uncertainty_does_not_call_any_positive_gap_clear(self):
        self.assertEqual(chunks.median_interval(list(range(72))), [27, 44])
        self.assertIsNone(chunks.median_interval([0, 1]))
        # A baseline below the median can still lie inside its interval.
        lo, hi = chunks.median_interval(list(range(72)))
        self.assertLess(lo, 34)
        self.assertGreater(hi, 34)


class Generation(unittest.TestCase):
    def vocabulary(self):
        return chunks.Overlap({'stripes': entry(['..gg..gg'] * 8)})

    def test_exact_overlap_determinism_padding_and_keep(self):
        vocab = self.vocabulary()
        exits = chunks.exit_segments(7, 5, ['N', 'S'])
        args = (vocab, 7, 5, exits, dict(open=.5, mirror=1), {(0, 0): ('g', '3')}, 17)
        rows, sources = chunks.ChunkSolver(*args).solve()
        again, _ = chunks.ChunkSolver(*args).solve()
        self.assertEqual(rows, again)
        self.assertEqual(rows[0][0], 'g')
        self.assertEqual((len(rows[0]), len(rows)), (7, 5))
        self.assertEqual(rows.elevation, ['3' * 7] * 5)
        padded, _ = chunks.ChunkSolver(vocab, 8, 8, chunks.exit_segments(8, 8, ['N']),
                                       dict(open=.5, mirror=1), seed=17).solve()
        for _, _, a, z in chunks.overlap_cut(padded):
            self.assertIn((a, z), vocab.patterns)
        self.assertTrue(all(s['source']['map'] == 'stripes' for s in sources))

    def test_shared_elevation_is_hard_not_independently_painted(self):
        a, b = entry(['....'] * 8, 'A'), entry(['....'] * 8, 'B')
        b['elevation'] = ['4444'] * 8
        vocab = chunks.Overlap({'A': a, 'B': b})
        rows, _ = chunks.ChunkSolver(vocab, 4, 8, chunks.exit_segments(4, 8, ['N', 'S']),
                                     dict(open=1, mirror=1), {(0, 0): ('.', '3')}).solve()
        self.assertEqual(rows.elevation, ['3333'] * 8)

    def test_no_buildings_and_whole_layout_holdout(self):
        maps = {'A': entry(['....'] * 4, 'A'), 'Alias': entry(['....'] * 4, 'A'),
                'B': entry(['gggg'] * 4, 'B'), 'House': entry(['bbbb'] * 4, 'HOUSE')}
        vocab = chunks.Overlap(maps, exclude=['A'])
        self.assertEqual(vocab.patterns, [('g' * 16, '3' * 16)])
        self.assertEqual({s['map'] for sources in vocab.sources for s in sources}, {'B'})

    def test_dead_ends_backtrack_instead_of_accepting_plain_lawn(self):
        vocab = chunks.Overlap({'lawn': entry(['.' * 64] * 64, 'LAWN'),
                                 'stripes': entry(['..gg..gg'] * 8, 'STRIPES')})
        solver = chunks.ChunkSolver(vocab, 8, 8, chunks.exit_segments(8, 8, ['N', 'S']),
                                    dict(open=.25, mirror=1), seed=1)
        rows, _ = solver.solve()
        self.assertTrue(any('g' in r for r in rows))
        self.assertGreater(solver.rejects['plain screen'], 0)
        self.assertGreater(solver.backtracks, 0)

    def test_partial_connectivity_and_single_water_constraints(self):
        solver = chunks.ChunkSolver(self.vocabulary(), 4, 4, chunks.exit_segments(4, 4, ['N', 'S']),
                                    dict(open=1, mirror=1))
        for kind, tokens in (
            ('exit connectivity', {(x, y): frozenset(['T3' if y == 2 else '.3']) for y in range(4) for x in range(4)}),
            ('single water cell', {(x, y): frozenset(['~3' if (x, y) == (0, 1) else '.3']) for y in range(4) for x in range(4)}),
            ('water connectivity', {(x, y): frozenset(['~3' if x == 0 and y in (0, 3) else '.3']) for y in range(4) for x in range(4)})):
            with self.subTest(kind=kind), patch.object(solver, 'options', return_value=tokens):
                self.assertEqual(solver.constraint(solver.domains), kind)

    def test_mirror_and_plain_limits_are_hard(self):
        solver = chunks.ChunkSolver(self.vocabulary(), 4, 4, chunks.exit_segments(4, 4, ['N']),
                                    dict(open=1, mirror=.5))
        tokens = {(x, y): frozenset(['g3' if x in (1, 2) else '.3']) for y in range(4) for x in range(4)}
        with patch.object(solver, 'options', return_value=tokens):
            self.assertEqual(solver.constraint(solver.domains), 'mirror')
        solver.limits = dict(open=.1, mirror=1)
        tokens = {(x, y): frozenset(['.3']) for y in range(4) for x in range(4)}
        with patch.object(solver, 'options', return_value=tokens):
            self.assertEqual(solver.constraint(solver.domains), 'plain screen')

    def test_keep_and_exit_conflict_is_not_silently_carved(self):
        text = blockout.serialize(['?T??'] + ['????'] * 3)
        keep = chunks.parse_keep(text, 4, 4)
        self.assertEqual(keep, {(1, 0): ('T', None)})
        solver = chunks.ChunkSolver(self.vocabulary(), 4, 4, chunks.exit_segments(4, 4, ['N']),
                                    dict(open=1, mirror=1), keep)
        with self.assertRaisesRegex(PkError, 'no fallback'):
            solver.solve()


class Paths(unittest.TestCase):
    def test_blob_normalization_has_47_masks(self):
        masks = {chunks.blob_mask('p', ['p' if raw & (1 << i) else '.' for i in range(8)]) for raw in range(256)}
        self.assertEqual(len(masks), 47)

    def test_t_crossing_and_dead_end_take_own_learned_mask(self):
        for cardinal, mt in (([0, 2, 6], 101), ([0, 2, 4, 6], 102), ([0], 103)):
            rows = [list('.....') for _ in range(5)]
            rows[2][2] = 'p'
            for i in cardinal:
                dx, dy = chunks.MASK_OFFSETS[i]
                rows[2 + dy][2 + dx] = 'p'
            rows = [''.join(row) for row in rows]
            mask = chunks.blob_mask('p', chunks.around_cell(rows, 2, 2))
            table = {('p', '*', mask): Counter({(mt, 0, 3): 4})}
            with self.subTest(mask=mask), patch.object(chunks, 'autotile_table', return_value=table):
                domains, _, _ = chunks.mask_domains(None, None, None, rows)
                self.assertEqual(set(domains[2, 2]), {(mt, 0, 3)})

    def test_diagonal_only_path_warns_even_with_canonical_mask(self):
        rows = ['p..', '.p.', '...']
        with patch.object(chunks, 'autotile_table', return_value={('p', '*', 0): Counter({(101, 0, 3): 1})}):
            domains, notes, _ = chunks.mask_domains(None, None, None, rows)
        self.assertFalse(domains[1, 1])
        self.assertIn('WARN BLOCKOUT reshape 1,1 (no vanilla tile for this path/edge shape)', notes)

    def test_context_mask_intersection_and_explicit_mask_only_override(self):
        for mask_mt in (8, 9):
            rules = SimpleNamespace(context3={}, context4={}, letters={'p': Counter({(8, 0, 3): 1})},
                                    pair=SimpleNamespace(behavior_name=lambda mt: 'NORMAL'),
                                    mask_domains={(0, 0): Counter({(mask_mt, 0, 3): 5})})
            domains = blockout.ContextDomains(['p'], rules, frozenset())
            self.assertEqual(domains[0, 0], [mask_mt])
            if mask_mt == 9:
                self.assertTrue(any('exact-mask override' in s for s in domains.history))

    def test_mst_network_connects_all_endpoints_before_fill(self):
        grid = SimpleNamespace(w=20, h=32, data={})
        exits = chunks.exit_segments(20, 32, ['N', 'S'])
        network, edges = chunks.path_network(grid, exits, {}, [(5, 10)], 11)
        self.assertEqual(len(edges), 2)
        targets = set().union(*exits.values()) | {(5, 10)}
        seen, todo = {min(network)}, [min(network)]
        while todo:
            x, y = todo.pop()
            for p in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if p in network and p not in seen:
                    seen.add(p)
                    todo.append(p)
        self.assertTrue(targets <= seen)
        self.assertEqual(network, chunks.path_network(grid, exits, {}, [(5, 10)], 11)[0])
        self.assertTrue(all(0 <= x < 20 and 0 <= y < 32 for x, y in network))


def run_briefs():
    """Sequential acceptance driver; first missed D target stops all later work."""
    from pk.core.repo import Repo
    from pk.core.journal import Transaction
    from pk.maps.edit import load
    from pk.maps import layout
    from pk.maps.reach import World, STATES
    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location('chunks_cli', root / 'tools/pk/pk.py')
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    reference = Repo(root)
    shared = blockout.reference(reference)
    reports = []

    def save_report(reason=None):
        tx = Transaction(reference, 'layout acceptance report')
        tx.stage('.pk/chunks/briefs.json', (json.dumps(dict(briefs=reports, stopped=reason), indent=2) + '\n').encode())
        tx.commit()

    def stop(reason):
        print('FAIL STOP', reason, flush=True)
        save_report(reason)
        raise SystemExit(1)

    def run(args, audit):
        print('COMMAND python tools/pk/pk.py ' + ' '.join(args), flush=True)
        tx = Transaction(reference, ' '.join(args))
        state = Repo(root, tx)
        state._blockout_reference = shared
        try:
            result, _ = cli.dispatch(state, tx, args)
        except PkError as exc:
            print(f'ERR {exc.code} {exc}', *exc.data, sep='\n', flush=True)
            stop(str(exc))
        tx.stage('.pk/chunks/' + audit + '.log', (result.render() + '\n').encode('utf-8'))
        tx.commit()
        print(result.summary, flush=True)
        for line in result.data:
            if line.startswith(('INFO LAYOUT path MST', 'INFO LAYOUT generation', 'INFO LAYOUT anti-blandness',
                                'INFO OBJECT', 'INFO BLOCKOUT fidelity', 'INFO BLOCKOUT exact-mask',
                                'INFO BLOCKOUT new SEAM', 'WARN OBJECT', 'WARN reach', 'WARN BLOCKOUT bank')):
                print(line, flush=True)
        if not result.ok:
            stop(result.summary)
        return result

    cases = [('PkLayoutCodexCoast_Frlg', 'N', 'coast', '10,10'),
             ('PkLayoutCodexForest_Frlg', 'N,S', 'forest', None),
             ('PkLayoutCodexSevii_Frlg', 'W', 'sevii', '18,12')]
    for n, (name, exits, keep, via) in enumerate(cases, 1):
        file = f'.pk/chunks/layout_{n}.blockout'
        args = ['layout', 'generate', name, '--type', 'route', '--exits', exits, '--seed', '1',
                '--keep', f'.pk/chunks/{keep}.keep', '--out', file]
        if via:
            args += ['--via', via]
        run(args, f'layout_{n}_generate')
        check = run(['map', 'blockout', name, file, '--check'], f'layout_{n}_check')
        shape = sum(int(line.split('+')[1].split()[0]) if '+' in line else 1
                    for line in check.data if line.startswith('INFO BLOCKOUT SHAPE'))
        reports.append(dict(map=name, shape=shape))
        print(f'MEASURE {name} SHAPE:{shape} target:<=6', flush=True)
        if shape > 6:
            stop(name + ' SHAPE exceeds 6')
        run(['layout', 'check-paths', file, '--map', name], f'layout_{n}_masks')
        run(['map', 'blockout', name, file], f'layout_{n}_tile')
        grid, pair = load(reference, name)
        metadata = json.loads(reference.read(file + '.json'))
        exits_set = {tuple(p) for ps in metadata['exits'].values() for p in ps}
        points = exits_set | {tuple(p) for p in metadata['network']['cells']}
        reached = World(grid, pair, grid.data).reach({min(exits_set)}, STATES['none'])
        missing = points - reached
        reports[-1]['unreachable'] = len(missing)
        if missing:
            stop(name + f' unreachable network/exit cells:{len(missing)}')
        lint = run(['map', 'lint', name, '--all'], f'layout_{n}_lint')
        bad = [line for line in lint.data if line.startswith(('INFO LAYOUT open', 'INFO LAYOUT mirror'))]
        if bad:
            stop(name + ' ' + '; '.join(bad))
        run(['map', 'render', name, '--scale', '2', '--out', f'.pk/render/layout_{n}.png'], f'layout_{n}_render')
        reports[-1].update(safe=True, layout_rows=0, render=f'.pk/render/layout_{n}.png')
        save_report()
    print('PASS D briefs. Seed comparison and E rebuild validation follow.', flush=True)
    save_report()


if __name__ == '__main__':
    if '--briefs' in sys.argv:
        run_briefs()
    else:
        unittest.main()
