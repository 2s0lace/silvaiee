import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HOME = Path(__file__).resolve().parents[1]
ROOT = HOME.parents[1]
sys.path.insert(0, str(HOME))
from pk.core.errors import PkError
from pk.core.repo import Repo
from pk.core.journal import Transaction, undo
from pk.text.charmap import Charmap, glyph_widths, Measure
from pk.text.strings import blocks, replace, structure, to_plain, wrap
from pk.text.ops import Text, TEXT_GLOBS, calibrate

spec = importlib.util.spec_from_file_location('pk_cli_text', HOME/'pk.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)

MAP = 'PalletTown_Frlg'
SCRIPTS = f'data/maps/{MAP}/scripts.inc'


def text_files():
    found = set()
    for pattern in TEXT_GLOBS:
        found.update(ROOT.glob(pattern))
    return sorted(found)


class VanillaCorpus(unittest.TestCase):
    """Read-only checks over every .string block in the real project."""
    @classmethod
    def setUpClass(cls):
        cls.text = Text(Repo(ROOT))
        cls.corpus = [(p, p.read_bytes().decode('utf-8')) for p in text_files()]

    def test_every_block_tokenizes(self):
        count = 0
        for path, content in self.corpus:
            for block in blocks(content):
                self.text.charmap.tokens(block.source)
                count += 1
        self.assertGreater(count, 10000)

    def test_reemit_is_byte_identical(self):
        total = different = 0
        for path, content in self.corpus:
            for block in blocks(content):
                total += 1
                different += replace(content, block, block.source) != content
        # 13 vanilla blocks put two control codes on one .string line; pk only rewrites edited blocks.
        self.assertLessEqual(different, total * 0.002)

    def test_wrap_fits_and_never_needs_more_lines_than_vanilla(self):
        t = self.text
        checked = exact = 0
        for path, content in self.corpus:
            for block in blocks(content):
                src = block.source
                if '{' in src or not src.endswith('$') or t.font(src) != 'FONT_NORMAL':
                    continue
                vanilla = structure(src)
                if any(not ''.join(box).strip() for box in vanilla):
                    continue   # vanilla empty boxes ("...\p$") are not expressible in plain input
                if any(t.measure.text(line) > t.limit for box in vanilla for line in box):
                    continue
                out, boxes = wrap(to_plain(src), t.measure, t.limit)
                self.assertTrue(all(t.measure.text(line) <= t.limit for box in boxes for line in box), block.label)
                self.assertEqual(len(boxes), len(vanilla), block.label)
                for ours, theirs in zip(boxes, vanilla):
                    self.assertLessEqual(len(ours), len(theirs), block.label)
                checked += 1
                exact += out == src
        self.assertGreater(checked, 5000)
        # Vanilla often breaks early for meaning ("Me?\nI'm the programmer!"), so exact
        # reproduction is not a goal (DECISIONS.md); greedy wrap must never be worse.
        self.assertGreater(exact, checked * 0.05)

    def test_calibration_finds_208(self):
        tx = Transaction(Repo(ROOT), 'test calibrate')
        result = calibrate(Repo(ROOT, tx), tx)
        self.assertIn('line_px:208', result.summary)
        self.assertEqual(tx.commit(dry_run=True), None)


class Wrapping(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cm = Charmap((ROOT/'charmap.txt').read_bytes())
        cls.m = Measure(cm, glyph_widths((ROOT/'src/fonts.c').read_text(encoding='utf-8')), {'PLAYER': 42})

    def test_styles(self):
        out, boxes = wrap('One two || three | four five six seven eight nine ten eleven twelve thirteen', self.m, 208)
        self.assertTrue(out.startswith('One two\\pthree\\nfour five'))
        self.assertIn('\\l', out)
        self.assertTrue(out.endswith('$'))

    def test_placeholder_with_space_is_one_word(self):
        out, _ = wrap('The {COLOR BLUE}sea{COLOR DARK_GRAY} is calm.', self.m, 208)
        self.assertEqual(out, 'The {COLOR BLUE}sea{COLOR DARK_GRAY} is calm.$')

    def test_errors(self):
        with self.assertRaises(PkError) as ctx:
            wrap('x' * 60, self.m, 208)
        self.assertEqual(ctx.exception.code, 'TEXTFIT')
        with self.assertRaises(PkError) as ctx:
            wrap('a\\nb', self.m, 208)
        self.assertEqual(ctx.exception.code, 'PARSE')


class Commands(unittest.TestCase):
    """Mutating commands run against a minimal copy of the project."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for rel in ['charmap.txt', 'src/fonts.c', 'build_fr.sh', 'data/layouts/layouts.json',
                    'data/maps/map_groups.json', f'data/maps/{MAP}/map.json', SCRIPTS,
                    'tools/pk/data/text_limits.json']:
            (self.root/rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/rel, self.root/rel)
        self.original = (self.root/SCRIPTS).read_bytes()

    def run_pk(self, *args):
        output, code = cli.main(list(args), root=self.root)
        return output, code

    def test_get(self):
        output, code = self.run_pk('text', 'get', 'PalletTown_Text_OakDontGoOut')
        self.assertEqual(code, 0, output)
        self.assertIn('[1.1] OAK: Hey! Wait!', output)
        self.assertIn('[1.2] Don\'t go out!', output)

    def test_set_touches_only_the_block_and_undoes(self):
        output, code = self.run_pk('text', 'set', 'PalletTown_Text_OakDontGoOut',
                                   'OAK: Hey! Wait! Not without a POKéMON! || The grass is dangerous.')
        self.assertEqual(code, 0, output)
        self.assertLessEqual(len(output), 300)
        new = (self.root/SCRIPTS).read_bytes()
        old_lines, new_lines = self.original.split(b'\n'), new.split(b'\n')
        start = old_lines.index(b'PalletTown_Text_OakDontGoOut::')
        self.assertEqual(old_lines[:start+1], new_lines[:start+1])
        self.assertEqual(old_lines[start+3:], new_lines[start+1+len(new_lines)-len(old_lines)+2:])
        self.assertIn(b'\t.string "The grass is dangerous.$"', new)
        undo(Repo(self.root))
        self.assertEqual((self.root/SCRIPTS).read_bytes(), self.original)

    def test_dry_run_writes_nothing(self):
        output, code = self.run_pk('text', 'set', 'PalletTown_Text_OakDontGoOut', 'Short.', '--dry-run')
        self.assertEqual(code, 0, output)
        self.assertEqual((self.root/SCRIPTS).read_bytes(), self.original)

    def test_add_and_duplicate(self):
        output, code = self.run_pk('text', 'add', MAP, 'PalletTown_Text_PkNew', 'Hello, {PLAYER}!')
        self.assertEqual(code, 0, output)
        self.assertTrue((self.root/SCRIPTS).read_bytes().endswith(b'\nPalletTown_Text_PkNew::\n\t.string "Hello, {PLAYER}!$"\n'))
        output, code = self.run_pk('text', 'add', MAP, 'PalletTown_Text_PkNew', 'Again')
        self.assertIn('ERR CONFLICT', output)

    def test_keep_breaks_and_errors(self):
        output, code = self.run_pk('text', 'set', 'PalletTown_Text_OakDontGoOut', 'Line one\\nLine two', '--keep-breaks')
        self.assertEqual(code, 0, output)
        self.assertIn(b'\t.string "Line one\\n"\n\t.string "Line two$"', (self.root/SCRIPTS).read_bytes())
        output, _ = self.run_pk('text', 'set', 'PalletTown_Text_OakDontGoOut', 'He said "hi"')
        self.assertIn('ERR PARSE', output)
        output, _ = self.run_pk('text', 'get', 'NoSuchLabelAnywhereAtAll')
        self.assertTrue(output.startswith('ERR'))

    def test_replace_scoped(self):
        output, code = self.run_pk('text', 'replace', 'tall grass', 'high grass', '--scope', MAP)
        self.assertEqual(code, 0, output)
        self.assertRegex(output, r'text replace [1-9]\d* texts 1 files')
        self.assertIn(b'high grass', (self.root/SCRIPTS).read_bytes())
        self.assertNotIn(b'tall grass', (self.root/SCRIPTS).read_bytes())


if __name__ == '__main__':
    unittest.main()
