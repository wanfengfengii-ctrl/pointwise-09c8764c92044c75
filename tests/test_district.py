from dataclasses import replace

from tests.support import DAY, EAST, WEST, Interrupted, WarehouseCase, correction, interrupt, opening
from warehouse import CorrectionBatch, apply_batch, encode_edition, open_day, open_district, read_balance, read_edition, receive_edition
from warehouse.district.inbox import load_pending_edition
from warehouse.district.postings import load_posted_entries, load_posting, read_frontier
from warehouse.district.receipts import load_receipt


class DistrictTests(WarehouseCase):
    def _with_gap(self):
        open_day(self.source, opening())
        apply_batch(self.source, CorrectionBatch(DAY, 'empty-2'))
        apply_batch(self.source, correction(district=None))
        east = self.open_district(EAST)
        self.send(east, EAST, 1)
        return east

    def _altered(self, material):
        updated = replace(material.changes[-1], litres=12)
        return encode_edition(replace(material, changes=(*material.changes[:-1], updated)))

    def test_gap_and_empty_edition(self):
        east = self._with_gap()
        late = self.send(east, EAST, 3)
        self.assertEqual((late.status, late.frontier, late.completed), ('pending', 1, ()))
        self.assertIsNone(late.receipt)
        self.assertEqual(read_balance(east, DAY, EAST).total_litres, 7)
        self.assertIsNone(load_receipt(east, DAY, EAST, 3))
        finish = self.send(east, EAST, 2)
        self.assertEqual([result.edition for result in finish.completed], [2, 3])
        empty = read_edition(east, DAY, EAST, 2)
        self.assertEqual((empty.entries, empty.row_count, empty.total_litres), ((), 1, 7))
        self.assertEqual((empty.totals.entered_litres, empty.totals.exited_litres), (0, 0))
        self.assertEqual(read_edition(east, DAY, EAST, 3).total_litres, 11)

    def test_pending_reopen(self):
        east = self._with_gap()
        draft = self.send(east, EAST, 3)
        east.close()
        east = self.open_district(EAST)
        self.assertEqual(self.send(east, EAST, 3), draft)
        self.assertEqual(read_frontier(east, DAY, EAST), 1)
        self.assertEqual(read_balance(east, DAY, EAST).total_litres, 7)
        self.assertIsNone(load_receipt(east, DAY, EAST, 3))
        self.assertIsNotNone(load_pending_edition(east, DAY, EAST, 3))
        self.assertEqual(self.send(east, EAST, 2).frontier, 3)
        self.assertIsNone(load_pending_edition(east, DAY, EAST, 3))

    def test_same_body_distinct_editions(self):
        east = self._with_gap()
        apply_batch(self.source, CorrectionBatch(DAY, 'empty-4'))
        self.assertEqual(self.material(EAST, 2).changes, self.material(EAST, 4).changes)
        second = self.send(east, EAST, 2)
        self.send(east, EAST, 3)
        fourth = self.send(east, EAST, 4)
        self.assertEqual((second.receipt.edition, fourth.receipt.edition, fourth.frontier), (2, 4, 4))
        self.assertNotEqual(second.receipt.token, fourth.receipt.token)
        fourth_again = self.send(east, EAST, 4)
        self.assertEqual(fourth_again.receipt, fourth.receipt)
        self.assertEqual(fourth_again.completed, ())

    def test_duplicate_and_altered_edition(self):
        open_day(self.source, opening())
        apply_batch(self.source, correction())
        east, west = self.open_district(EAST), self.open_district(WEST)
        for db, name in ((east, EAST), (west, WEST)):
            self.send(db, name, 1)
        departure = self.send(east, EAST, 2)
        arrival = self.send(west, WEST, 2)
        duplicate = self.send(west, WEST, 2)
        self.assertEqual((departure.result.total_litres, arrival.result.total_litres), (0, 11))
        self.assertEqual([(row.action, row.litres, row.source.edition) for row in departure.result.entries],
                         [('exit', 7, 1)])
        self.assertEqual([(row.action, row.litres, row.source.edition) for row in arrival.result.entries],
                         [('enter', 11, 2)])
        self.assertEqual(duplicate.result, arrival.result)
        self.assertEqual(duplicate.completed, ())
        with self.assertRaisesRegex(ValueError, 'conflicting material for posted'):
            receive_edition(west, self._altered(self.material(WEST, 2)))
        self.assertEqual(read_balance(west, DAY, WEST).total_litres, 11)
        self.assertEqual(read_edition(west, DAY, WEST, 2), arrival.result)

    def test_pending_conflict(self):
        east = self._with_gap()
        self.send(east, EAST, 3)
        with self.assertRaisesRegex(ValueError, 'conflicting material for pending'):
            receive_edition(east, self._altered(self.material(EAST, 3)))
        self.assertEqual(read_frontier(east, DAY, EAST), 1)
        self.assertIsNone(load_receipt(east, DAY, EAST, 3))
        self.send(east, EAST, 2)
        self.assertEqual((read_balance(east, DAY, EAST).total_litres,
                          read_edition(east, DAY, EAST, 3).entries[-1].litres), (11, 11))

    def test_wrong_scope(self):
        open_day(self.source, opening())
        east = self.open_district(EAST)
        original = self.send(east, EAST, 1)
        with self.assertRaisesRegex(ValueError, 'different district'):
            self.send(east, WEST, 1)
        with self.assertRaisesRegex(ValueError, 'different owner'):
            open_district(self.root / 'east.sqlite', WEST)
        with self.assertRaises(ValueError):
            receive_edition(east, encode_edition(self.material(EAST, 1)) + b'\n')
        self.assertEqual(read_edition(east, DAY, EAST, 1), original.result)
        self.assertEqual(read_balance(east, DAY, EAST).total_litres, 7)

    def test_failure_before_commit(self):
        open_day(self.source, opening())
        apply_batch(self.source, correction(district=None))
        east = self.open_district(EAST)
        first = self.send(east, EAST, 1)

        def after_writes():
            self.assertEqual(len(load_posted_entries(east, DAY, EAST, 2)), 2)
            self.assertIsNotNone(load_receipt(east, DAY, EAST, 2))
            self.assertEqual(read_balance(east, DAY, EAST).total_litres, 11)
            interrupt()

        with self.assertRaises(Interrupted):
            self.send(east, EAST, 2, before_commit=after_writes)
        east.close()
        east = self.open_district(EAST)
        self.assertIsNone(load_posting(east, DAY, EAST, 2))
        self.assertEqual(load_posted_entries(east, DAY, EAST, 2), ())
        self.assertIsNone(load_receipt(east, DAY, EAST, 2))
        self.assertEqual(read_edition(east, DAY, EAST, 1), first.result)
        self.assertEqual(read_balance(east, DAY, EAST).total_litres, 7)
        retry = self.send(east, EAST, 2)
        self.assertEqual((retry.frontier, retry.result.row_count, retry.result.total_litres), (2, 1, 11))
        self.assertEqual(len(retry.result.entries), 2)
        self.assertEqual(self.send(east, EAST, 2).receipt, retry.receipt)

    def test_failed_gap_drain_preserves_prior_inbox(self):
        east = self._with_gap()
        self.send(east, EAST, 3)
        with self.assertRaises(Interrupted):
            self.send(east, EAST, 2, before_commit=interrupt)
        east.close()
        east = self.open_district(EAST)
        self.assertEqual(read_frontier(east, DAY, EAST), 1)
        self.assertIsNone(load_pending_edition(east, DAY, EAST, 2))
        self.assertIsNotNone(load_pending_edition(east, DAY, EAST, 3))
        posted = self.send(east, EAST, 2)
        self.assertEqual([(x.edition, x.total_litres) for x in posted.completed], [(2, 7), (3, 11)])

    def test_days_and_large_exact_totals_are_independent(self):
        next_day = '2026-02-04'
        maximum = 2**63 - 1
        open_day(self.source, opening(litres=maximum, second=maximum))
        open_day(self.source, opening(litres=0, day=next_day))
        east = self.open_district(EAST)
        later = self.send(east, EAST, 1, next_day)
        earlier = self.send(east, EAST, 1)
        self.assertEqual((later.result.row_count, later.result.total_litres), (1, 0))
        self.assertEqual((earlier.result.row_count, earlier.result.total_litres), (2, 18446744073709551614))
        self.assertEqual(read_balance(east, DAY, EAST).total_litres, 18446744073709551614)
        self.assertEqual(read_edition(east, next_day, EAST, 1), later.result)
        self.assertNotEqual(earlier.receipt.token, later.receipt.token)
