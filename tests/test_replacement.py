import json
from dataclasses import replace
from pathlib import Path

from tests.support import DAY, EAST, Interrupted, STATION, WarehouseCase, interrupt
from warehouse import (
    CorrectionBatch, MembershipInput, ObservationInput, OpeningBatch, ReplacementBatch,
    SourcePosition, SourceReference, ValueCorrection, apply_batch, apply_replacement,
    encode_edition, expire_input, open_day, project_day, read_balance, read_edition,
    read_replacement_origins, receive_edition, release_edition,
)
from warehouse.intake.requests import lookup_request
from warehouse.intake.source_rows import list_expired_rows
from warehouse.ledger.journal import list_formal_editions

ORDERS = json.loads(Path(__file__).resolve().parent.parent.joinpath('replacement-orders.json')
                    .read_text(encoding='utf-8'))


def position(value):
    return SourcePosition(value[0], value[1])


def reference(day, value):
    return SourceReference(day, value[0], value[1], SourcePosition(value[2], value[3]))


def observations(day, items):
    return tuple(ObservationInput(key, ORDERS['station'], litres, position(row))
                 for key, litres, row in items)


def allocations(day, items):
    return tuple((retired, new, reference(day, origin), litres)
                 for retired, new, origin, litres in items)


def order(day, spec, *, allocation_key='allocations'):
    return ReplacementBatch(
        day, spec['request_id'], ORDERS['station'],
        tuple((key, reference(day, value)) for key, value in spec['retired']),
        observations(day, spec['new']),
        allocations(day, ORDERS[allocation_key] if allocation_key not in spec else spec[allocation_key]),
    )


def opening_batch(day):
    source = ORDERS['opening']
    return OpeningBatch(
        day, source['request_id'], tuple(ORDERS['districts']),
        observations(day, source['observations']),
        (MembershipInput(ORDERS['station'], source['district'],
                         position(source['membership_position'])),))


class ReplacementTests(WarehouseCase):
    def _opened(self, day=DAY):
        open_day(self.source, opening_batch(day))
        return day

    def _first(self, day=DAY):
        return order(day, ORDERS['orders'][0])

    def _second(self, day=DAY, allocation_key='allocations'):
        return order(day, ORDERS['orders'][1], allocation_key=allocation_key)

    def test_work_orders_and_origins(self):
        day = self._opened()
        first = apply_replacement(self.source, self._first())
        second = apply_replacement(self.source, self._second())
        self.assertEqual((first.edition, second.edition), (2, 3))
        self.assertEqual([(p.district, p.row_count, p.total_litres) for p in second.projections],
                         [(EAST, 2, 12), ('west', 0, 0)])
        east = self.open_district(EAST)
        for number in (1, 2, 3):
            self.send(east, EAST, number)
        self.assertEqual(read_balance(east, day, EAST).total_litres, 12)
        self.assertEqual(read_replacement_origins(east, day, EAST, 2, 'c'),
                         ((reference(day, [1, 'morning', 'morning.csv', 1]), 1),
                          (reference(day, [1, 'morning', 'morning.csv', 2]), 5)))
        self.assertEqual(read_replacement_origins(east, day, EAST, 2, 'd'),
                         ((reference(day, [1, 'morning', 'morning.csv', 1]), 6),))
        self.assertEqual(read_replacement_origins(east, day, EAST, 3, 'e'),
                         ((reference(day, [1, 'morning', 'morning.csv', 1]), 3),))
        self.assertEqual(read_replacement_origins(east, day, EAST, 3, 'f'),
                         ((reference(day, [1, 'morning', 'morning.csv', 1]), 4),
                          (reference(day, [1, 'morning', 'morning.csv', 2]), 5)))
        for number, key in ((2, 'e'), (2, 'a'), (3, 'c'), (1, 'a')):
            with self.subTest(number=number, key=key), self.assertRaises(LookupError):
                read_replacement_origins(east, day, EAST, number, key)

    def test_out_of_order_delivery(self):
        day = self._opened()
        apply_replacement(self.source, self._first())
        apply_replacement(self.source, self._second())
        east = self.open_district(EAST)
        self.send(east, EAST, 1)
        late = self.send(east, EAST, 3)
        self.assertEqual((late.status, late.frontier, late.completed, late.receipt),
                         ('pending', 1, (), None))
        finish = self.send(east, EAST, 2)
        self.assertEqual([(item.edition, item.total_litres) for item in finish.completed],
                         [(2, 12), (3, 12)])
        self.assertEqual(finish.frontier, 3)
        self.assertEqual(self.send(east, EAST, 2).receipt, read_edition(east, day, EAST, 2).receipt)
        self.assertEqual(self.send(east, EAST, 3).receipt, read_edition(east, day, EAST, 3).receipt)

    def test_bad_order_rejected_by_source(self):
        day = self._opened()
        apply_replacement(self.source, self._first())
        with self.assertRaises(ValueError):
            apply_replacement(self.source, self._second(allocation_key='bad_second_allocations'))
        self.assertEqual([item.number for item in list_formal_editions(self.source, day)], [1, 2])
        self.assertIsNone(lookup_request(self.source, day, ORDERS['orders'][1]['request_id']))
        self.assertEqual(len(list_expired_rows(self.source, day, 3)), 5)

    def test_bad_order_rejected_by_district(self):
        day = self._opened()
        apply_replacement(self.source, self._first())
        apply_replacement(self.source, self._second())
        alternate = self.open_source_file('alternate.sqlite')
        open_day(alternate, opening_batch(day))
        apply_replacement(alternate, order(day, ORDERS['orders'][0],
                                           allocation_key='alternate_first_allocations'))
        apply_replacement(alternate, self._second(allocation_key='bad_second_allocations'))
        honest = {number: encode_edition(release_edition(self.source, day, EAST, number))
                  for number in (1, 2)}
        forged = encode_edition(release_edition(alternate, day, EAST, 3))
        east = self.open_district(EAST)
        receive_edition(east, honest[1])
        second = receive_edition(east, honest[2])
        balance = read_balance(east, day, EAST)
        with self.assertRaisesRegex(ValueError, 'exit shares'):
            receive_edition(east, forged)
        self.assertEqual(read_balance(east, day, EAST), balance)
        self.assertEqual(read_edition(east, day, EAST, 2), second.result)
        self.assertEqual(self.send(east, EAST, 2).receipt, second.receipt)

    def test_origins_survive_expiry_and_reopen(self):
        day = self._opened()
        apply_replacement(self.source, self._first())
        apply_replacement(self.source, self._second())
        payloads = {(district, number): encode_edition(release_edition(self.source, day, district, number))
                    for district in (EAST, 'west') for number in (1, 2, 3)}
        self.assertEqual(expire_input(self.source, day, 3).removed, 7)
        self.assertEqual(list_expired_rows(self.source, day, 3), ())
        self.source.close()
        self.source = self.open_source()
        east = self.open_district(EAST)
        for number in (1, 2, 3):
            receive_edition(east, payloads[EAST, number])
        original = read_replacement_origins(east, day, EAST, 3, 'f')
        entry = read_edition(east, day, EAST, 3)
        east.close()
        east = self.open_district(EAST)
        self.assertEqual(read_replacement_origins(east, day, EAST, 3, 'f'), original)
        self.assertEqual(read_edition(east, day, EAST, 3), entry)
        self.assertEqual(receive_edition(east, payloads[EAST, 3]).receipt, entry.receipt)

    def test_replay_and_conflict(self):
        day = self._opened()
        first = self._first()
        original = apply_replacement(self.source, first)
        self.assertEqual(apply_replacement(self.source, first), original)
        with self.assertRaisesRegex(ValueError, 'conflicting parameters'):
            apply_replacement(self.source, replace(first, new=first.new[:1]))
        with self.assertRaisesRegex(ValueError, 'conflicting parameters'):
            apply_batch(self.source, CorrectionBatch(day, first.request_id))
        self.assertEqual([item.number for item in list_formal_editions(self.source, day)], [1, 2])

    def test_replacement_retires_and_revision_provenance(self):
        day = self._opened()
        apply_replacement(self.source, self._first())
        apply_batch(self.source, CorrectionBatch(day, 'empty', (),
                                                (MembershipInput(STATION, 'west',
                                                                 SourcePosition('stations.csv', 2)),)))
        rows = [(row.observation, row.litres, row.source.edition, row.source.request_id)
                for projection in project_day(self.source, day) for row in projection.rows]
        self.assertEqual(rows, [('c', 6, 2, 'shift'), ('d', 6, 2, 'shift')])
        with self.assertRaises(ValueError):
            apply_replacement(self.source, self._second())
        self.assertEqual([item.number for item in list_formal_editions(self.source, day)], [1, 2, 3])

    def test_cross_district_replacement_qualification(self):
        day = self._opened()
        apply_replacement(self.source, self._first())
        apply_batch(self.source, CorrectionBatch(day, 'move', (),
                                                (MembershipInput(STATION, 'west',
                                                                 SourcePosition('stations.csv', 2)),)))
        with self.assertRaisesRegex(ValueError, 'generation district'):
            apply_replacement(self.source, ReplacementBatch(
                day, 'west-shift', STATION,
                (('c', reference(day, [2, 'shift', 'shift.csv', 1])),),
                (ObservationInput('g', STATION, 6, SourcePosition('shift.csv', 5)),),
                (('c', 'g', reference(day, [1, 'morning', 'morning.csv', 1]), 6),)))
        self.assertEqual([item.number for item in list_formal_editions(self.source, day)], [1, 2, 3])

    def test_ordinary_value_correction_rebases_origin(self):
        day = self._opened()
        apply_replacement(self.source, self._first())
        apply_batch(self.source, CorrectionBatch(day, 'revalue',
                                                (ValueCorrection('c', 6,
                                                                 SourcePosition('c-fix.csv', 9)),), ()))
        apply_batch(self.source, CorrectionBatch(day, 'move', (),
                                                (MembershipInput(STATION, 'west',
                                                                 SourcePosition('stations.csv', 2)),)))
        west = release_edition(self.source, day, 'west', 4)
        self.assertIsNone(west.replacement)
        self.assertEqual([(item.observation, item.action) for item in west.changes],
                         [('c', 'enter'), ('d', 'enter')])
        result = apply_replacement(self.source, ReplacementBatch(
            day, 'west-shift', STATION,
            (('c', reference(day, [3, 'revalue', 'c-fix.csv', 9])),),
            (ObservationInput('g', STATION, 6, SourcePosition('shift.csv', 5)),),
            (('c', 'g', reference(day, [3, 'revalue', 'c-fix.csv', 9]), 6),)))
        self.assertEqual(result.edition, 5)

    def test_zero_litre_replacement(self):
        day = self._opened()
        apply_batch(self.source, CorrectionBatch(
            day, 'empty-out', (ValueCorrection('a', 0, SourcePosition('empty.csv', 1)),), ()))
        zero = ReplacementBatch(
            day, 'zero', ORDERS['station'],
            (('a', reference(day, [2, 'empty-out', 'empty.csv', 1])),),
            (ObservationInput('z', ORDERS['station'], 0, SourcePosition('zero.csv', 1)),), ())
        result = apply_replacement(self.source, zero)
        self.assertEqual(result.edition, 3)
        self.assertEqual([(row.observation, row.litres)
                          for projection in result.projections for row in projection.rows],
                         [('b', 5), ('z', 0)])
        east = self.open_district(EAST)
        for number in (1, 2, 3):
            self.send(east, EAST, number)
        self.assertEqual(read_replacement_origins(east, day, EAST, 3, 'z'), ())
        material = release_edition(self.source, day, EAST, 3)
        self.assertEqual(material.replacement.allocations, ())
        self.assertEqual([(item.action, item.observation, item.litres) for item in material.changes],
                         [('exit', 'a', 0), ('enter', 'z', 0)])

    def test_rollback_and_retry(self):
        day = self._opened()
        first = self._first()
        with self.assertRaises(Interrupted):
            apply_replacement(self.source, first, before_commit=interrupt)
        self.source.close()
        self.source = self.open_source()
        self.assertIsNone(lookup_request(self.source, day, first.request_id))
        self.assertEqual([item.number for item in list_formal_editions(self.source, day)], [1])
        self.assertEqual(len(list_expired_rows(self.source, day, 2)), 3)
        self.assertEqual(apply_replacement(self.source, first).edition, 2)

        east = self.open_district(EAST)
        self.send(east, EAST, 1)
        with self.assertRaises(Interrupted):
            self.send(east, EAST, 2, before_commit=interrupt)
        east.close()
        east = self.open_district(EAST)
        self.assertEqual(read_balance(east, day, EAST).total_litres, 12)
        self.assertEqual(self.send(east, EAST, 2).completed[0].edition, 2)
        self.assertEqual(read_balance(east, day, EAST).total_litres, 12)

    def open_source_file(self, name):
        from warehouse import open_source
        db = open_source(self.root / name)
        self.addCleanup(db.close)
        return db


class ReplacementQualificationTests(WarehouseCase):
    """Regression coverage for the district-side generation qualification."""

    def _opened(self):
        open_day(self.source, opening_batch(DAY))
        apply_replacement(self.source, order(DAY, ORDERS['orders'][0]))

    def _deliver(self, district, last, first=1):
        ledger = self.open_district(district)
        for number in range(first, last + 1):
            self.send(ledger, district, number)
        return ledger

    def test_moving_out_of_generation_district_disables_allocation(self):
        self._opened()
        apply_batch(self.source, CorrectionBatch(DAY, 'west-bound', (), (
            MembershipInput(ORDERS['station'], 'west', SourcePosition('stations.csv', 2)),)))
        west = self._deliver('west', 3)
        self.assertEqual(read_balance(west, DAY, 'west').total_litres, 12)
        with self.assertRaisesRegex(ValueError, 'generation district'):
            apply_replacement(self.source, ReplacementBatch(
                DAY, 'forbidden', ORDERS['station'],
                (('c', reference(DAY, [2, 'shift', 'shift.csv', 1])),),
                (ObservationInput('g', ORDERS['station'], 6, SourcePosition('g.csv', 1)),),
                (('c', 'g', reference(DAY, [1, 'morning', 'morning.csv', 1]), 6),)))

    def test_same_value_correction_restores_ordinary_origin(self):
        self._opened()
        apply_batch(self.source, CorrectionBatch(DAY, 'equal-value', (
            ValueCorrection('c', 6, SourcePosition('c-fix.csv', 2)),), ()))
        east = self._deliver(EAST, 3)
        result = apply_replacement(self.source, ReplacementBatch(
            DAY, 'continued', ORDERS['station'],
            (('c', reference(DAY, [3, 'equal-value', 'c-fix.csv', 2])),),
            (ObservationInput('g', ORDERS['station'], 6, SourcePosition('g.csv', 1)),),
            (('c', 'g', reference(DAY, [3, 'equal-value', 'c-fix.csv', 2]), 6),)))
        self.assertEqual(result.edition, 4)
        self.send(east, EAST, 4)
        self.assertEqual(read_replacement_origins(east, DAY, EAST, 4, 'g'),
                         ((reference(DAY, [3, 'equal-value', 'c-fix.csv', 2]), 6),))

    def test_replacement_cannot_pad_with_unsplit_share(self):
        self._opened()
        with self.assertRaisesRegex(ValueError, 'original origins'):
            apply_replacement(self.source, ReplacementBatch(
                DAY, 'padded', ORDERS['station'],
                (('c', reference(DAY, [2, 'shift', 'shift.csv', 1])),),
                (ObservationInput('g', ORDERS['station'], 5, SourcePosition('g.csv', 1)),),
                (('c', 'g', reference(DAY, [1, 'morning', 'morning.csv', 1]), 5),)))

    def test_replacement_of_replacement_requires_received_shares(self):
        self._opened()
        with self.assertRaisesRegex(ValueError, 'original origins'):
            apply_replacement(self.source, ReplacementBatch(
                DAY, 'swap-origins', ORDERS['station'],
                (('c', reference(DAY, [2, 'shift', 'shift.csv', 1])),),
                (ObservationInput('g', ORDERS['station'], 6, SourcePosition('g.csv', 1)),),
                (('c', 'g', reference(DAY, [1, 'morning', 'morning.csv', 2]), 6),)))
