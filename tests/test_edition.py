from tests.support import DAY, EAST, WEST, WarehouseCase, correction, opening
from warehouse import apply_batch, decode_edition, encode_edition, open_day
from warehouse.edition.artifacts import load_frozen_edition


class EditionTests(WarehouseCase):
    def test_reassignment_of_untouched_row(self):
        open_day(self.source, opening(second=5))
        apply_batch(self.source, correction())
        east = self.material(EAST, 2)
        west = self.material(WEST, 2)
        self.assertEqual([(c.action, c.observation, c.litres) for c in east.changes],
                         [('exit', 'a', 7), ('exit', 'b', 5)])
        self.assertEqual([(c.action, c.observation, c.litres) for c in west.changes],
                         [('enter', 'a', 11), ('enter', 'b', 5)])
        self.assertEqual(east.changes[1].source, west.changes[1].source)
        self.assertEqual(west.changes[1].source.position.row, 2)
        self.assertEqual(sum(c.litres for c in west.changes), 16)

    def test_old_and_new_provenance(self):
        open_day(self.source, opening(second=5))
        apply_batch(self.source, correction())
        east = self.material(EAST, 2)
        west = self.material(WEST, 2)

        def origins(material):
            return [(c.action, c.observation, c.litres, c.source.edition, c.source.request_id,
                     c.source.position.document, c.source.position.row) for c in material.changes]

        self.assertEqual(origins(east), [('exit', 'a', 7, 1, 'morning', 'morning.csv', 1),
                                         ('exit', 'b', 5, 1, 'morning', 'morning.csv', 2)])
        self.assertEqual(origins(west), [('enter', 'a', 11, 2, 'fix', 'fix-observations.csv', 4),
                                         ('enter', 'b', 5, 1, 'morning', 'morning.csv', 2)])
        self.assertEqual(decode_edition(encode_edition(west)), west)

    def test_release_replay_and_replacement_order(self):
        open_day(self.source, opening())
        apply_batch(self.source, correction(district=None))
        original = encode_edition(self.material(EAST, 2))
        self.assertEqual([(c.action, c.litres) for c in decode_edition(original).changes],
                         [('exit', 7), ('enter', 11)])
        apply_batch(self.source, correction('later', litres=13))
        self.source.close()
        self.source = self.open_source()
        self.assertEqual(encode_edition(self.material(EAST, 2)), original)

    def test_invalid_release(self):
        open_day(self.source, opening())
        apply_batch(self.source, correction())
        for district, number in (('north', 2), (EAST, 3)):
            with self.subTest(district=district, number=number), self.assertRaises(LookupError):
                self.material(district, number)
        self.source.execute('DELETE FROM observation_revisions WHERE day = ? AND edition = 2', (DAY,))
        with self.assertRaisesRegex(RuntimeError, 'incomplete observation_revisions'):
            self.material(WEST, 2)
        self.assertIsNone(load_frozen_edition(self.source, DAY, WEST, 2))
        self.assertEqual(self.material(EAST, 1).changes[0].litres, 7)
