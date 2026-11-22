from tests.support import DAY, EAST, WEST, WarehouseCase, correction, opening
from warehouse import apply_batch, encode_edition, expire_input, open_day, read_balance, read_edition, receive_edition
from warehouse.intake.source_rows import list_expired_rows


class AuditTests(WarehouseCase):
    def test_expiration_then_offline_read(self):
        original = open_day(self.source, opening())
        apply_batch(self.source, correction())
        frozen = {(district, number): encode_edition(self.material(district, number))
                  for district, number in ((EAST, 1), (WEST, 1), (WEST, 2))}
        blocked = expire_input(self.source, DAY, 1)
        self.assertEqual((blocked.removed, blocked.blocked), (0, ((2, EAST),)))
        self.assertEqual(len(list_expired_rows(self.source, DAY, 2)), 4)
        frozen[EAST, 2] = encode_edition(self.material(EAST, 2))
        cleared = expire_input(self.source, DAY, 1)
        self.assertEqual((cleared.removed, cleared.blocked), (2, ()))
        self.assertEqual(list_expired_rows(self.source, DAY, 1), ())
        self.assertEqual([row.edition for row in list_expired_rows(self.source, DAY, 2)], [2, 2])
        self.assertEqual(encode_edition(self.material(EAST, 2)), frozen[EAST, 2])
        self.source.close()
        self.source = self.open_source()
        self.assertEqual(open_day(self.source, opening()), original)
        self.assertEqual(list_expired_rows(self.source, DAY, 1), ())
        self.assertEqual(expire_input(self.source, DAY, 2).removed, 2)
        self.assertEqual(list_expired_rows(self.source, DAY, 2), ())
        self.source.close()

        for district, expected in ((EAST, ('exit', 7, 0, 'morning.csv')),
                                   (WEST, ('enter', 11, 11, 'fix-observations.csv'))):
            with self.subTest(district=district):
                local = self.open_district(district)
                receive_edition(local, frozen[district, 1])
                received = receive_edition(local, frozen[district, 2])
                local.close()
                local = self.open_district(district)
                recorded = read_edition(local, DAY, district, 2)
                row, = recorded.entries
                self.assertEqual((row.action, row.litres, recorded.total_litres, row.source.position.document), expected)
                self.assertEqual(recorded, received.result)
                self.assertEqual(receive_edition(local, frozen[district, 2]).receipt, recorded.receipt)

    def test_unposted_read(self):
        open_day(self.source, opening())
        apply_batch(self.source, correction())
        west = self.open_district(WEST)
        pending = self.send(west, WEST, 2)
        self.assertEqual(pending.status, 'pending')
        for district, number in ((WEST, 2), (WEST, 1), ('north', 2)):
            with self.subTest(district=district, number=number), self.assertRaises(LookupError):
                read_edition(west, DAY, district, number)
        self.send(west, WEST, 1)
        self.assertEqual(read_edition(west, DAY, WEST, 2).totals.net_litres, 11)

    def test_historical_after_later_edition(self):
        open_day(self.source, opening())
        apply_batch(self.source, correction())
        west = self.open_district(WEST)
        self.send(west, WEST, 1)
        original = self.send(west, WEST, 2).result
        self.assertEqual((original.row_count, original.total_litres, original.totals.entered_litres,
                          original.totals.exited_litres), (1, 11, 11, 0))
        apply_batch(self.source, correction('return', litres=None, district=EAST))
        frozen = self.freeze(3)
        self.assertEqual(expire_input(self.source, DAY, 3).removed, 5)
        self.source.close()
        receive_edition(west, frozen[WEST, 3])
        self.assertEqual(read_balance(west, DAY, WEST).total_litres, 0)
        west.close()
        west = self.open_district(WEST)
        self.assertEqual(read_edition(west, DAY, WEST, 2), original)
        later = read_edition(west, DAY, WEST, 3)
        self.assertEqual((later.row_count, later.total_litres, later.totals.net_litres), (0, 0, -11))
        self.assertEqual(later.entries[0].source, original.entries[0].source)

    def test_offline_reversed_reassignment_retains_both_origins(self):
        open_day(self.source, opening(second=5))
        apply_batch(self.source, correction())
        apply_batch(self.source, correction('return', litres=None, district=EAST))
        frozen = self.freeze(3)
        self.assertEqual(expire_input(self.source, DAY, 3).removed, 6)
        self.source.close()
        west = self.open_district(WEST)
        receive_edition(west, frozen[WEST, 1])
        third = receive_edition(west, frozen[WEST, 3])
        self.assertEqual((third.frontier, third.receipt, third.status), (1, None, 'pending'))
        west.close()
        west = self.open_district(WEST)
        self.assertEqual(read_balance(west, DAY, WEST).total_litres, 0)
        second = receive_edition(west, frozen[WEST, 2])
        self.assertEqual([(result.edition, result.row_count, result.total_litres) for result in second.completed],
                         [(2, 2, 16), (3, 0, 0)])
        exits = read_edition(west, DAY, WEST, 3)
        self.assertEqual([(row.position, row.action, row.observation, row.litres, row.source.position.document)
                          for row in exits.entries],
                         [(1, 'exit', 'a', 11, 'fix-observations.csv'), (2, 'exit', 'b', 5, 'morning.csv')])
        self.assertEqual(receive_edition(west, frozen[WEST, 3]).receipt, exits.receipt)
