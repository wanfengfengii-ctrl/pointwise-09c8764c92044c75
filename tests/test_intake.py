from dataclasses import replace

from tests.support import DAY, EAST, WEST, WarehouseCase, correction, opening
from warehouse import ObservationInput, apply_batch, open_day, project_day, project_district
from warehouse.intake.requests import lookup_request
from warehouse.intake.source_rows import list_expired_rows
from warehouse.ledger.journal import list_formal_editions


class IntakeTests(WarehouseCase):
    def test_open_day(self):
        batch = opening()
        initial = open_day(self.source, batch)
        self.assertEqual(initial, open_day(self.source, batch))
        self.assertEqual(initial.edition, 1)
        self.assertEqual([(p.district, p.row_count, p.total_litres) for p in initial.projections],
                         [(EAST, 1, 7), (WEST, 0, 0)])
        source = initial.projections[0].rows[0].source
        self.assertEqual((source.edition, source.request_id, source.position.document, source.position.row),
                         (1, 'morning', 'morning.csv', 1))
        self.assertEqual(len(list_formal_editions(self.source, DAY)), 1)
        self.assertEqual(len(list_expired_rows(self.source, DAY, 1)), 2)

    def test_request_conflict(self):
        original = open_day(self.source, opening())
        with self.assertRaisesRegex(ValueError, 'conflicting parameters'):
            open_day(self.source, opening(litres=9))
        with self.assertRaisesRegex(ValueError, 'already open'):
            open_day(self.source, replace(opening(), request_id='other-opening'))
        self.assertEqual(project_district(self.source, DAY, EAST).total_litres, 7)
        self.assertEqual(open_day(self.source, opening()), original)
        self.assertIsNone(lookup_request(self.source, DAY, 'other-opening'))

    def test_duplicate_position(self):
        batch = opening(second=5)
        a, b = batch.observations
        bad = replace(batch, observations=(a, ObservationInput(b.observation, b.station, b.litres, a.position)))
        with self.assertRaisesRegex(ValueError, 'duplicate source position'):
            open_day(self.source, bad)
        self.assertIsNone(lookup_request(self.source, DAY, 'morning'))
        self.assertEqual(list_expired_rows(self.source, DAY, 1), ())
        with self.assertRaises(LookupError):
            project_day(self.source, DAY)
        self.assertEqual(open_day(self.source, batch).projections[0].total_litres, 12)

    def test_reopen_and_user_revision(self):
        original = open_day(self.source, opening())
        changed = apply_batch(self.source, correction(district=None))
        self.source.close()
        self.source = self.open_source()
        self.assertEqual(open_day(self.source, opening()), original)
        self.assertEqual(project_district(self.source, DAY, EAST, 1).total_litres, 7)
        self.assertEqual(project_district(self.source, DAY, EAST).total_litres, 11)
        self.assertEqual(apply_batch(self.source, correction(district=None)), changed)
        self.assertEqual([e.number for e in list_formal_editions(self.source, DAY)], [1, 2])
