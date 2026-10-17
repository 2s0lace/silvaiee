"""map copies rename their own script labels; map delete removes a Pk* draft (in-memory, never committed)."""
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_maps import MapTest  # noqa: E402

from pk.core.errors import PkError  # noqa: E402
from pk.maps import create  # noqa: E402
from pk.maps.paintover import copy_map  # noqa: E402

SRC = 'PkPalletSolV2_Frlg'


class CopyRenamesLabels(MapTest):
    def test_copy_has_no_label_of_the_source(self):
        # Every polish --into copy kept SRC_MapScripts:: and the build died on duplicate symbols.
        copy_map(self.repo, self.tx, SRC, 'PkCopyTest_Frlg')
        script = self.tx.writes['data/maps/PkCopyTest_Frlg/scripts.inc'].decode('utf-8')
        self.assertIn('PkCopyTest_Frlg_MapScripts::', script)
        self.assertNotRegex(script, r'(?<![A-Za-z0-9_])' + re.escape(SRC) + '_')
        data = json.loads(self.tx.writes['data/maps/PkCopyTest_Frlg/map.json'])
        self.assertNotIn(SRC + '_', json.dumps(data))
        self.assertEqual(data['name'], 'PkCopyTest_Frlg')
        self.assertIn('data/maps/PkCopyTest_Frlg/scripts.inc',
                      self.tx.writes['data/event_scripts.s'].decode('utf-8'))


class DeleteDraft(MapTest):
    def test_delete_unregisters_everything(self):
        result = create.delete_map(self.repo, self.tx, SRC)
        self.assertIn('files:', result.summary)
        groups = json.loads(self.tx.writes['data/maps/map_groups.json'])
        self.assertFalse(any(SRC in groups[g] for g in groups['group_order']))
        self.assertNotIn(f'data/maps/{SRC}/scripts.inc', self.tx.writes['data/event_scripts.s'].decode('utf-8'))
        layouts = json.loads(self.tx.writes['data/layouts/layouts.json'])
        self.assertFalse(any(x['id'].startswith('LAYOUT_PK_PALLET_SOL_V2') for x in layouts['layouts']))
        self.assertIsNone(self.tx.writes[f'data/maps/{SRC}/map.json'])
        self.assertIsNone(self.tx.writes[f'data/layouts/{SRC}/map.bin'])

    def test_vanilla_is_refused(self):
        with self.assertRaises(PkError):
            create.delete_map(self.repo, self.tx, 'PalletTown_Frlg')


if __name__ == '__main__':
    unittest.main()
