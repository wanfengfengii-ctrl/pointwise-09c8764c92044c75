from dataclasses import replace

from tests.support import DAY, EAST, WEST, Interrupted, WarehouseCase, correction, interrupt, opening
from warehouse import apply_batch, open_day, project_day, project_district
from warehouse.intake.requests import lookup_request
from warehouse.intake.source_rows import list_expired_rows
from warehouse.ledger.journal import list_formal_editions


class LedgerTests(WarehouseCase):
    def test_atomic_correction(self):
        open_day(self.source, opening())
        result = apply_batch(self.source, correction())
        self.assertEqual(result.edition, 2)
        old_east, old_west = project_day(self.source, DAY, 1)
        new_east, new_west = project_day(self.source, DAY, 2)
        self.assertEqual((old_east.total_litres, old_west.total_litres), (7, 0))
        self.assertEqual((new_east.total_litres, new_west.total_litres), (0, 11))
        old, new = old_east.rows[0], new_west.rows[0]
        self.assertEqual((old.district, old.litres, old.source.edition), (EAST, 7, 1))
        self.assertEqual((new.district, new.litres, new.source.edition), (WEST, 11, 2))
        self.assertEqual(new.source.position.document, 'fix-observations.csv')

    def test_historical_membership(self):
        open_day(self.source, opening(second=5))
        apply_batch(self.source, correction())
        apply_batch(self.source, correction('return', litres=None, district=EAST))
        for number, district, litres in ((1, EAST, (7, 5)), (2, WEST, (11, 5)), (3, EAST, (11, 5))):
            with self.subTest(number=number):
                group = project_district(self.source, DAY, district, number)
                self.assertEqual([(row.district, row.litres) for row in group.rows],
                                 [(district, value) for value in litres])
                self.assertEqual(group.rows[1].source.edition, 1)

    def test_exact_measurement(self):
        open_day(self.source, opening(litres=0, second=9007199254740993))
        self.source.close()
        self.source = self.open_source()
        east = project_district(self.source, DAY, EAST)
        self.assertEqual([row.litres for row in east.rows], [0, 9007199254740993])
        self.assertEqual((east.row_count, east.total_litres), (2, 9007199254740993))
        for litres in (True, 1.0, None, -1, 2**63):
            with self.subTest(litres=litres), self.assertRaises(ValueError):
                apply_batch(self.source, correction(district=None, litres=litres if litres is not None else 0)
                            if litres is not None else replace(correction(district=None), observations=(
                                replace(correction(district=None).observations[0], litres=None),
                            )))
        self.assertEqual(len(list_formal_editions(self.source, DAY)), 1)

    def test_invalid_group(self):
        open_day(self.source, opening())
        with self.assertRaisesRegex(ValueError, 'unknown district'):
            apply_batch(self.source, correction(district='north'))
        batch = correction()
        bad_groups = (
            replace(batch, observations=(replace(batch.observations[0], observation='unknown'),)),
            replace(batch, observations=batch.observations * 2),
            replace(batch, memberships=batch.memberships * 2),
        )
        for bad in bad_groups:
            with self.subTest(batch=bad), self.assertRaises(ValueError):
                apply_batch(self.source, bad)
        self.assertEqual(project_district(self.source, DAY, EAST).total_litres, 7)
        self.assertEqual([e.number for e in list_formal_editions(self.source, DAY)], [1])
        self.assertIsNone(lookup_request(self.source, DAY, 'fix'))
        self.assertEqual(len(list_expired_rows(self.source, DAY, 2)), 2)

    def test_corrected_retry_and_submission_failure(self):
        open_day(self.source, opening())
        valid = correction()
        with self.assertRaises(ValueError):
            apply_batch(self.source, correction(district='north'))
        with self.assertRaises(Interrupted):
            apply_batch(self.source, valid, before_commit=interrupt)
        self.source.close()
        self.source = self.open_source()
        self.assertIsNone(lookup_request(self.source, DAY, 'fix'))
        self.assertEqual(len(list_expired_rows(self.source, DAY, 2)), 2)
        self.assertEqual(project_district(self.source, DAY, EAST).total_litres, 7)
        self.assertEqual(apply_batch(self.source, valid).edition, 2)
        self.assertEqual(project_district(self.source, DAY, WEST).total_litres, 11)
