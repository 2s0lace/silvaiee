"""Chunk grids, bounded merging, held-out evidence, and measurable seams."""
from pathlib import Path
import random
import sys
import unittest
from types import SimpleNamespace

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


if __name__ == '__main__':
    unittest.main()
