"""Milestone 5: pixel DSL, tileset compiler, export round trip (uncommitted transactions only)."""
from pathlib import Path
import struct
import sys
import unittest

HOME = Path(__file__).resolve().parents[1]
ROOT = HOME.parents[1]
sys.path.insert(0, str(HOME))
from pk.core.errors import PkError
from pk.core.repo import Repo
from pk.core.journal import Transaction
from pk.maps.meta import draw, all_ids
from pk.maps.tiles import Pair
from pk.art.compiler import build, gba
from pk.art.ops import export
from pk.art.pixeldsl import parse

ART = 'tools/pk/art/pk_test_forest'


def quantised(pixels):
    return [[gba(c) for c in row] for row in pixels]


class Art(unittest.TestCase):
    def setUp(self):
        self.tx = Transaction(Repo(ROOT), 'test')
        self.repo = Repo(ROOT, self.tx)

    def write(self, path, text):
        self.tx.stage(path, text.encode('utf-8'))

    def test_dsl_ops(self):
        self.write(f'{ART}/pal.px', 'pal a=#204020 b=#40a040 c=#80d080 d=#604020\n')
        self.write(f'{ART}/tree.px', '\n'.join([
            'size 16x32', 'use pal.px', 'layer top', 'rect 2,1 12x12 b', 'outline a', 'noise b,c 0.3 seed=1',
            'mirror x', 'layer bottom', 'fill c', 'grid 7,14', 'dd', 'dd', '', 'dither a,b 0,30 16x2']) + '\n')
        d = parse(self.repo, f'{ART}/tree.px')
        self.assertEqual((d.w, d.h), (16, 32))
        top = d.layers['top']
        self.assertEqual(top[0][2], (0x20, 0x40, 0x20))                 # outline above the rect
        for row in top:
            self.assertEqual(row, row[::-1])                                 # mirrored
        self.assertEqual(d.layers['bottom'][14][7], (0x60, 0x40, 0x20))
        self.assertEqual(d.layers['bottom'][30][0], (0x20, 0x40, 0x20))
        with self.assertRaises(PkError):
            self.write(f'{ART}/bad.px', 'size 16x16\nfill z\n')
            parse(self.repo, f'{ART}/bad.px')

    def test_build_new_tileset_and_register(self):
        self.write(f'{ART}/pal.px', 'pal g=#40a040 h=#80d080 t=#206020 k=#604020\n')
        self.write(f'{ART}/ground.px', 'size 32x16\nuse pal.px\nfill g\nnoise g,h 0.2 seed=2\nrect 16,0 16x16 k\n')
        self.write(f'{ART}/tree.px', 'size 16x16\nuse pal.px\nlayer bottom\nfill g\nlayer top\nrect 3,2 10x12 t\noutline k\n')
        self.write(f'{ART}/tileset.pkt', '\n'.join([
            'name pk_test_forest', 'kind secondary', 'version frlg', 'pair_with gTileset_General_Frlg',
            'grass ground.px@0,0 NORMAL NORMAL tags=ground sym=.',
            'dirt ground.px@16,0 NORMAL NORMAL tags=path sym==',
            'tree tree.px TALL_GRASS COVERED tags=tree sym=T']) + '\n')
        result = build(self.repo, self.tx, f'{ART}/tileset.pkt', {})
        self.assertIn('metatiles 3/384', result.render())
        folder = 'data/tilesets/secondary/pk_test_forest_frlg'
        attrs = struct.unpack('<3I', self.tx.writes[f'{folder}/metatile_attributes.bin'])
        self.assertEqual(attrs[2], 0x02 | 1 << 29)                          # TALL_GRASS, COVERED
        words = struct.unpack('<24H', self.tx.writes[f'{folder}/metatiles.bin'])
        # palette slots 0..12 (primary slots may be shared), tiles < 1024; pixels are checked via draw()
        self.assertTrue(all(w >> 12 <= 12 and (w & 0x3FF) < 1024 for w in words))
        self.assertTrue(any((w & 0x3FF) >= 640 for w in words))             # own tiles start at 640
        for header in ('graphics.h', 'metatiles.h', 'headers.h'):
            self.assertIn(b'PkTestForest', self.tx.writes[f'src/data/tilesets/{header}'])
        self.assertIn(b'extern const struct Tileset gTileset_PkTestForest;', self.tx.writes['include/tilesets.h'])
        # draw it back through the normal map pipeline
        repo = Repo(ROOT, self.tx)
        pair = Pair(repo, dict(id='T', primary_tileset='gTileset_General_Frlg',
                               secondary_tileset='gTileset_PkTestForest', layout_version='frlg'))
        tree = draw(repo, pair, 0x282)
        self.assertEqual(tree[8][8], (0x20, 0x60, 0x20))
        self.assertEqual(tree[0][0], (0x40, 0xa0, 0x40))
        # rebuilding keeps ids and does not register twice
        build(repo, self.tx, f'{ART}/tileset.pkt', {})
        self.assertEqual(self.tx.writes['src/data/tilesets/headers.h'].count(b'gTileset_PkTestForest ='), 1)

    def test_stamp_from_tileset_object(self):
        import json
        from pk.maps.create import stamp
        self.write(f'{ART}/pal.px', 'pal w=#c8b068 r=#c03040 k=#403830\n')
        self.write(f'{ART}/hut.px', 'size 48x32\nuse pal.px\nfill w\nlayer top\nrect 0,0 48x10 r\n')
        self.write(f'{ART}/tileset.pkt', '\n'.join(
            ['name pk_test_forest', 'kind secondary', 'version frlg', 'pair_with gTileset_General_Frlg'] +
            [f'hut_{x}_{y} hut.px@{x*16},{y*16} NORMAL NORMAL' for x in range(3) for y in range(2) if (x, y) != (2, 0)]) + '\n')
        build(self.repo, self.tx, f'{ART}/tileset.pkt', {})
        result = stamp(self.repo, self.tx, ['from-tileset', f'{ART}/tileset.pkt', 'hut.px', 'pk_test_hut'],
                       {'walk': '0,1'}, None).render()
        self.assertIn('stamp pk_test_hut 3x2', result)
        self.assertIn('##.', result)                                  # (2,0) not listed -> hole
        data = json.loads(self.tx.writes['tools/pk/data/stamps/pk_test_hut.json'])
        self.assertEqual((data['primary'], data['secondary']), ('gTileset_General_Frlg', 'gTileset_PkTestForest'))
        self.assertIsNone(data['cells'][0][2])
        self.assertEqual(data['cells'][1][0][1:], [0, 3])            # walkable
        self.assertEqual(data['cells'][0][0][1:], [1, 0])            # solid
        self.assertTrue(all(c is None or c[0] >= 640 for row in data['cells'] for c in row))
        with self.assertRaises(PkError):
            stamp(self.repo, self.tx, ['from-tileset', f'{ART}/tileset.pkt', 'hut.px', 'pk_test_hut'], {}, None)

    def test_sprite_export_build_register(self):
        import io
        from PIL import Image
        from pk.art import sprite
        sprite.command(self.repo, self.tx, ['export', 'WOMAN_1', 'pk_test_npc'], {})
        px = 'tools/pk/art/sprites/pk_test_npc.px'
        self.assertIn(px, self.tx.writes)
        result = sprite.command(self.repo, self.tx, ['build', px, 'pk_test_npc'], {'like': 'WOMAN_1'}).render()
        self.assertIn('OBJ_EVENT_GFX_PK_TEST_NPC registered', result)
        old = Image.open(ROOT/'graphics/object_events/pics/people/woman_1_frlg.png')
        new = Image.open(io.BytesIO(self.tx.writes['graphics/object_events/pics/people/pk_test_npc.png']))
        self.assertEqual(new.size, old.size)
        pal = lambda im, i: tuple(im.getpalette()[3 * i:3 * i + 3])
        self.assertEqual([pal(new, i) for i in new.tobytes()], [pal(old, i) for i in old.tobytes()])
        w = lambda p: self.tx.writes[p].decode('utf-8')
        consts = w('include/constants/event_objects.h')
        self.assertLess(consts.index('OBJ_EVENT_GFX_PK_TEST_NPC,'), consts.index('NUM_OBJ_EVENT_GFX,'))
        self.assertRegex(consts, r'OBJ_EVENT_GFX_PK_TEST_NPC,\n\s*NUM_OBJ_EVENT_GFX,')   # anchor line stays alone; gluing it breaks the next registration
        self.assertIn('const u16 gObjectEventPic_PkTestNpc[] = INCGFX_U16("graphics/object_events/pics/people/'
                      'pk_test_npc.png", ".4bpp", "-mwidth 2 -mheight 4");', w('src/data/object_events/object_event_graphics.h'))
        self.assertIn('overworld_ascending_frames(gObjectEventPic_PkTestNpc, 2, 4)',
                      w('src/data/object_events/object_event_pic_tables.h'))
        info = w('src/data/object_events/object_event_graphics_info.h')
        block = info[info.index('gObjectEventGraphicsInfo_PkTestNpc = {'):]
        self.assertIn('.images = sPicTable_PkTestNpc,', block[:900])
        self.assertIn('.paletteTag = OBJ_EVENT_PAL_TAG_NPC_GREEN,', block[:900])
        ptrs = w('src/data/object_events/object_event_graphics_info_pointers.h')
        self.assertIn('[OBJ_EVENT_GFX_PK_TEST_NPC] = &gObjectEventGraphicsInfo_PkTestNpc,', ptrs)
        self.assertIn('extern const struct ObjectEventGraphicsInfo gObjectEventGraphicsInfo_PkTestNpc;', ptrs)
        # rebuilding only replaces the png
        before = {k: v for k, v in self.tx.writes.items() if k.endswith('.h')}
        again = sprite.command(Repo(ROOT, self.tx), self.tx, ['build', px, 'pk_test_npc'], {'like': 'WOMAN_1'}).render()
        self.assertIn('already registered', again)
        self.assertEqual(before, {k: v for k, v in self.tx.writes.items() if k.endswith('.h')})
        # colours outside the template palette are refused
        self.write('tools/pk/art/sprites/pk_test_bad.px', 'size 160x32\npal z=#123456\nrect 0,0 4x4 z\n')
        with self.assertRaises(PkError) as ctx:
            sprite.command(self.repo, self.tx, ['build', 'tools/pk/art/sprites/pk_test_bad.px', 'pk_test_bad'],
                           {'like': 'WOMAN_1'})
        self.assertEqual(ctx.exception.code, 'LIMIT')

    def test_too_many_colours(self):
        colours = ' '.join(f'{c}=#{i*8:02x}2040' for i, c in enumerate('abcdefghijklmnopq'))
        rows = ['abcdefgh' + 'a' * 8, 'ijklmnop' + 'a' * 8, 'q' * 16] + ['a' * 16] * 13   # 17 colours in one 8x8
        self.write(f'{ART}/loud.px', 'size 16x16\npal ' + colours + '\ngrid\n' + '\n'.join(rows) + '\n')
        self.write(f'{ART}/loud.pkt', 'name pk_test_loud\nm loud.px NORMAL NORMAL\n')
        with self.assertRaises(PkError) as ctx:
            build(self.repo, self.tx, f'{ART}/loud.pkt', {})
        self.assertEqual(ctx.exception.code, 'LIMIT')

    def test_export_build_round_trip_pallet_town(self):
        """Export vanilla gTileset_PalletTown, rebuild it as a new tileset: every metatile must
        render pixel-identical (after 15-bit quantisation) with identical attribute words."""
        export(self.repo, self.tx, 'PalletTown_Frlg', 'secondary', 'pk_test_rt')
        build(self.repo, self.tx, 'tools/pk/art/pk_test_rt/tileset.pkt', {})
        old = Pair(Repo(ROOT), dict(id='A', primary_tileset='gTileset_General_Frlg',
                                    secondary_tileset='gTileset_PalletTown', layout_version='frlg'))
        repo = Repo(ROOT, self.tx)
        new = Pair(repo, dict(id='B', primary_tileset='gTileset_General_Frlg',
                              secondary_tileset='gTileset_PkTestRt', layout_version='frlg'))
        ids = [m for m in all_ids(old) if m >= 0x280]
        self.assertEqual(len(ids), new.count(new.secondary))
        for mt in ids:
            for slots in (range(4), range(4, 8)):
                self.assertEqual(quantised(draw(Repo(ROOT), old, mt, set(slots))),
                                 quantised(draw(repo, new, mt, set(slots))), hex(mt))
        self.assertEqual(old.attributes(old.secondary), new.attributes(new.secondary))


if __name__ == '__main__':
    unittest.main()


class Lint(unittest.TestCase):
    """pk tileset lint flags hand-drawn copies of animated vanilla art, holes and flat fills."""
    def test_lint_rules(self):
        from pk.art.lint import lint, Vanilla, animated_ranges
        tx = Transaction(Repo(ROOT), 'test')
        repo = Repo(ROOT, tx)
        self.assertIn((416, 48), animated_ranges(repo, 'gTileset_General_Frlg'))     # sea + shores
        self.assertIn((508, 4), animated_ranges(repo, 'gTileset_General_Frlg'))      # flowers
        sea = draw(repo, Pair(repo, dict(id='x', primary_tileset='gTileset_General_Frlg',
                                         secondary_tileset='gTileset_PalletTown', layout_version='frlg')), 0x12B)
        sea = quantised(sea)                                                      # the game's 15-bit colours
        colours = sorted({c for row in sea for c in row if c})
        sym = {c: 'abcdefghijklmnop'[i] for i, c in enumerate(colours)}
        exact = '\n'.join(''.join(sym[c] for c in row) for row in sea)
        pal = 'pal ' + ' '.join(f'{sym[c]}=#{c[0]:02x}{c[1]:02x}{c[2]:02x}' for c in colours)
        self.assertTrue(all(c[0] % 8 == 0 for c in colours))
        redrawn = exact.replace('a', 'b', 6)                                      # a few pixels off
        tx.stage(f'{ART}/sea_ok.px', f'size 16x16\n{pal}\ngrid\n{exact}\n'.encode())
        tx.stage(f'{ART}/sea_bad.px', f'size 16x16\n{pal}\ngrid\n{redrawn}\n'.encode())
        tx.stage(f'{ART}/hole.px', f'size 16x16\n{pal}\nfill b\ndot 3,3 .\nrect 8,8 8x8 a\n'.encode())
        tx.stage(f'{ART}/t.pkt', b'name pk_test_lint\npair_with gTileset_General_Frlg\n'
                 b'ok sea_ok.px NORMAL NORMAL\nbad sea_bad.px NORMAL NORMAL\nhole hole.px NORMAL NORMAL\n')
        _, warnings = lint(repo, f'{ART}/t.pkt')
        self.assertFalse([w for w in warnings if w.startswith('ok ')], warnings)
        self.assertTrue([w for w in warnings if w.startswith('bad ') and ('ANIMATED' in w)], warnings)
        self.assertTrue([w for w in warnings if w.startswith('hole') and 'transparent' in w], warnings)
        self.assertTrue([w for w in warnings if w.startswith('hole') and 'flat' in w], warnings)


class Import(unittest.TestCase):
    def test_blurred_upscale_round_trips(self):
        """A vanilla tree, upscaled 24x, blurred and noised like an AI image, imports pixel-exact."""
        import io, random
        from PIL import Image, ImageFilter
        from pk.art.importer import run
        tx = Transaction(Repo(ROOT), 'test')
        repo = Repo(ROOT, tx)
        pair = Pair(repo, dict(id='x', primary_tileset='gTileset_General_Frlg', secondary_tileset='gTileset_PalletTown',
                               layout_version='frlg'))
        img = Image.new('RGBA', (32, 32), (0, 0, 0, 0))
        tiles = (0x01C, 0x01D, 0x014, 0x015)
        for i, m in enumerate(tiles):
            for y, row in enumerate(draw(repo, pair, m)):
                for x, c in enumerate(row):
                    if c:
                        img.putpixel((x + 16 * (i % 2), y + 16 * (i // 2)), (*c, 255))
        big = img.resize((768, 768), Image.NEAREST).filter(ImageFilter.GaussianBlur(1.5))
        rnd, px = random.Random(1), big.load()
        for _ in range(20000):
            x, y = rnd.randrange(768), rnd.randrange(768)
            c = px[x, y]
            px[x, y] = tuple(min(255, max(0, v + rnd.randint(-10, 10))) for v in c[:3]) + (c[3],)
        buf = io.BytesIO()
        big.save(buf, 'PNG')
        tx.stage(f'{ART}/in.png', buf.getvalue())
        run(repo, tx, f'{ART}/in.png', f'{ART}/out.px', {'size': '32x32'})
        d = parse(repo, f'{ART}/out.px').layers['bottom']
        for i, m in enumerate(tiles):
            for y, row in enumerate(draw(repo, pair, m)):
                for x, c in enumerate(row):
                    self.assertEqual(d[y + 16 * (i // 2)][x + 16 * (i % 2)], gba(c) if c else None)
