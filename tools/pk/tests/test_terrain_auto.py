"""pk terrain auto: fill a box from its surroundings, no material given (in-memory, never committed)."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_maps import MapTest  # noqa: E402

from pk.maps import terrain  # noqa: E402
from pk.maps.edit import load  # noqa: E402
from pk.maps.layers import class_of  # noqa: E402


class TerrainAuto(MapTest):
    def test_shore_comes_back_from_surroundings(self):
        # Wipe Pallet's shore corner (ground | sea) with plain grass, then let auto refill it.
        self.map('paint', 'PalletTown_Frlg', '5,16,4x3', '0x010')
        result = terrain.auto(self.repo, self.tx, 'PalletTown_Frlg', '5,16,4x3')
        after = int(re.search(r'seams:\d+->(\d+)', result.summary)[1])
        self.assertEqual(after, 0, result.render())
        grid, pair = load(self.repo, 'PalletTown_Frlg')
        classes = {class_of(pair, *grid.at(x, y)[:2])[1] for y in range(16, 19) for x in range(5, 9)}
        # A transition between the town ground and the sea, not a flood of one material.
        self.assertTrue({'ground', 'water'} <= classes, (classes, result.render()))

    def test_no_doors_and_box_only(self):
        grid, pair = load(self.repo, 'PalletTown_Frlg')
        outside = {(x, y): grid.at(x, y) for y in range(grid.h) for x in range(grid.w)
                   if not (5 <= x < 9 and 16 <= y < 19)}
        terrain.auto(self.repo, self.tx, 'PalletTown_Frlg', '5,16,4x3')
        grid, pair = load(self.repo, 'PalletTown_Frlg')
        for p, cell in outside.items():
            self.assertEqual(grid.at(*p), cell, f'{p} outside the box changed')
        for y in range(16, 19):
            for x in range(5, 9):
                self.assertNotIn('DOOR', pair.behavior_name(grid.at(x, y)[0]))

    def test_options_writes_nothing_and_pick_is_deterministic_and_deduplicates(self):
        from pk.core.repo import Repo
        from pk.core.journal import Transaction
        # 1. --options 3 on a wiped shore corner computes 3 distinct fills and stages no map writes.
        self.map('paint', 'PalletTown_Frlg', '5,16,4x3', '0x010')
        painted_writes = dict(self.tx.writes)
        preview = terrain.auto(self.repo, self.tx, 'PalletTown_Frlg', '5,16,4x3', options_count=3)
        self.assertEqual(self.tx.writes, painted_writes)
        opt_lines = [r for r in preview.data if r.startswith('option ')]
        cmd_lines = [r for r in preview.data if r.startswith('pk terrain auto ')]
        self.assertEqual(len(opt_lines), 3, preview.data)
        self.assertEqual(cmd_lines, [f'pk terrain auto PalletTown_Frlg 5,16,4x3 --pick {i}' for i in (1, 2, 3)])
        for i in (1, 2, 3):
            self.assertTrue(self.repo.path(f'.pk/render/auto_PalletTown_Frlg_{i}.png').is_file())
        # 2. --pick n applies the exact option deterministically and two runs match byte-for-byte.
        chosen = []
        for i in (1, 2, 3):
            tx1 = Transaction(Repo(self.repo.root), 'p1')
            r1 = Repo(self.repo.root, tx1)
            tx1.writes.update(painted_writes)
            terrain.auto(r1, tx1, 'PalletTown_Frlg', '5,16,4x3', pick=i)
            tx2 = Transaction(Repo(self.repo.root), 'p2')
            r2 = Repo(self.repo.root, tx2)
            tx2.writes.update(painted_writes)
            terrain.auto(r2, tx2, 'PalletTown_Frlg', '5,16,4x3', pick=i)
            self.assertEqual(tx1.writes, tx2.writes)
            chosen.append(tx1.writes['data/layouts/PalletTown_Frlg/map.bin'])
        self.assertEqual(len(set(chosen)), 3)
        # 3. Identical options are deduplicated and reported with INFO only K distinct options.
        tx_dedupe = Transaction(Repo(self.repo.root), 'dedupe')
        r_dedupe = Repo(self.repo.root, tx_dedupe)
        deduped = terrain.auto(r_dedupe, tx_dedupe, 'PalletTown_Frlg', '5,16,4x3', options_count=3)
        self.assertIn('INFO only 2 distinct options', deduped.data)


class RepairKeepsPiece(MapTest):
    def test_copy_repair_never_changes_the_pasted_piece(self):
        # Benchmark v2: --repair rewrote small pasted pieces (sea became lawn). Now the interior of the
        # piece never changes, and its 1-tile rim only to a tile of the same physical class.
        source, _ = load(self.repo, 'Route21_North_Frlg')
        sx, sy, w, h = 4, 28, 6, 4
        piece = [source.at(sx + i, sy + j) for j in range(h) for i in range(w)]
        result = self.map('copy', 'PalletTown_Frlg', f'{sx},{sy},{w}x{h}', '6,15',
                          **{'from': 'Route21_North_Frlg', 'repair': True})
        grid, pair = load(self.repo, 'PalletTown_Frlg')
        from pk.maps.lint import physical_class
        phys = lambda c: (physical_class(pair.behavior_name(c[0]), c[1]), c[2])
        for j in range(h):
            for i in range(w):
                old, new = piece[j * w + i], grid.at(6 + i, 15 + j)
                if 0 < i < w - 1 and 0 < j < h - 1:
                    self.assertEqual(new, old, f'interior {i},{j} changed')          # interior: untouched
                else:
                    self.assertEqual(phys(new), phys(old), f'rim {i},{j} changed class')  # rim: same class
        self.assertIn('repair:', result.summary)

    def test_repair_warns_when_over_thirty_percent_of_rim_changes(self):
        # Triggering case: Route21_North_Frlg 4,28,6x4 -> PalletTown_Frlg 6,15 changes 15 of 16 rim cells (93.8%).
        bad = self.map('copy', 'PalletTown_Frlg', '4,28,6x4', '6,15',
                       **{'from': 'Route21_North_Frlg', 'repair': True})
        self.assertIn('WARN repair changed 15 of 16 rim cells; the piece may not fit here', bad.data)

    def test_repair_stays_quiet_when_rim_fits(self):
        # Non-triggering case: Route21_North_Frlg 11,1,4x2 -> PalletTown_Frlg 14,17 changes 1 of 8 rim cells (12.5%).
        good = self.map('copy', 'PalletTown_Frlg', '11,1,4x2', '14,17',
                        **{'from': 'Route21_North_Frlg', 'repair': True})
        self.assertFalse(any(r.startswith('WARN repair changed') for r in good.data), good.data)

    def test_copy_repair_honours_a_frozen_set(self):
        # A piece pasted just below a protected area: its repair ring reaches rows y=14..15, which
        # repair rewrites unless those cells are passed as `frozen` (blueprints pass keep | stamped |
        # footprint). `frozenset()` is the control: the seed must still reach keep, or the test lies.
        from pk.core.repo import Repo
        from pk.core.journal import Transaction
        from pk.maps import edit
        keep = {(x, y) for y in range(12, 16) for x in range(4, 10)}

        def changed(frozen):
            tx = Transaction(Repo(self.repo.root), 'frozen')
            repo = Repo(self.repo.root, tx)
            base, _ = load(repo, 'PalletTown_Frlg')
            before = {p: base.at(*p) for p in keep}
            edit.command(repo, tx, ['copy', 'PalletTown_Frlg', '4,28,6x4', '4,16'],
                         {'from': 'Route21_North_Frlg', 'repair': True, 'frozen': frozen})
            grid, _ = load(repo, 'PalletTown_Frlg')
            return [p for p in keep if grid.at(*p) != before[p]]

        self.assertEqual(changed(frozenset(keep)), [])
        self.assertTrue(changed(frozenset()), 'the fixture no longer reaches keep; repair is not exercised')


class ReachWriteTest(MapTest):
    def test_wall_across_pallet_south_exit_warns_with_right_count(self):
        # A wall at y=16 severs the town from its south exit: the warps and edge up lose the
        # 47-cell south bank, and edge down loses the whole north town, so the union is reported.
        result = self.map('paint', 'PalletTown_Frlg', '0,16,24x1', '0x01D')
        self.assertEqual(result.data[-1], 'WARN reach: 245 cells cut off, e.g. 2,2 3,2 4,2 5,2 6,2')
        # A subsequent harmless write compares before vs after this write only.
        second = self.map('paint', 'PalletTown_Frlg', '3,3', '0x004')
        self.assertEqual(second.data[-1], 'reach: ok')

    def test_wall_splitting_town_in_two_warns(self):
        # A solid wall at x=12 severs warp#0 (west house) from warp#1/#2 (east house, lab) and the
        # north road; unioning the warps used to hide it because the union still reached both halves.
        result = self.map('paint', 'PalletTown_Frlg', '12,0,1x20', '0x01D')
        self.assertEqual(result.data[-1], 'WARN reach: 242 cells cut off, e.g. 2,2 3,2 4,2 5,2 6,2')

    def test_wall_sealing_north_exit_warns(self):
        # Painting y=0 solid walls the only exit to Route1 (edge up, cells 12,0 and 13,0). The
        # entrance disappears from the new map; edge entrances used to be ignored whenever warps
        # existed, so this stayed 'reach: ok'.
        result = self.map('paint', 'PalletTown_Frlg', '0,0,24x1', '0x01D')
        self.assertEqual(result.data[-1], 'WARN reach: 2 cells cut off, e.g. 12,0 13,0')

    def test_harmless_writes_and_no_entrance_info(self):
        from pk.maps import create
        ok_paint = self.map('paint', 'PalletTown_Frlg', '3,3', '0x004')
        self.assertEqual(ok_paint.data[-1], 'reach: ok')
        ok_grid = self.map('grid', 'PalletTown_Frlg', '3,3', 'def A 0x004;A')
        self.assertEqual(ok_grid.data[-1], 'reach: ok')
        ok_copy = self.map('copy', 'PalletTown_Frlg', '5,12,2x1', '3,3')
        self.assertEqual(ok_copy.data[-1], 'reach: ok')
        ok_auto = terrain.auto(self.repo, self.tx, 'PalletTown_Frlg', '3,3,2x2')
        self.assertEqual(ok_auto.data[-1], 'reach: ok')
        ok_brush = terrain.command(self.repo, self.tx, ['brush', 'PalletTown_Frlg', 'rect', '3,3,2x2', 'ground'],
                                   {}, lambda o, d=20: (0, d))
        self.assertEqual(ok_brush.data[-1], 'reach: ok')
        ok_stamp = create.stamp(self.repo, self.tx, ['place', 'PalletTown_Frlg', 'tree', '2,2'],
                                {}, lambda o, d=20: (0, d))
        self.assertEqual(ok_stamp.data[-1], 'reach: ok')
        no_ent = self.map('paint', 'PkPalletSolV2_Frlg', '3,3', '0x004')
        self.assertEqual(no_ent.data[-1], 'INFO reach: not checked (no entrance)')


class RepeatLintTest(MapTest):
    def test_repeat_finds_non_overlapping_mixed_4x4_blocks_and_is_info_only(self):
        from pk.maps import lint
        res = lint.run(self.repo, 'PkPalletSolV2_Frlg', show_all=True)
        repeats = [r for r in res.data if r.startswith('INFO REPEAT ')]
        self.assertEqual(repeats, [
            'INFO REPEAT 0,24 8,24 16,24 4x4',
            'INFO REPEAT 1,24 9,24 17,24 4x4',
            'INFO REPEAT 2,24 10,24 18,24 4x4',
        ])
        # Uniform 4x4 blocks (single physical class) do not trigger INFO REPEAT.
        pal = lint.run(self.repo, 'PalletTown_Frlg', show_all=True)
        self.assertEqual([r for r in pal.data if r.startswith('INFO REPEAT ')], [])


if __name__ == '__main__':
    unittest.main()
