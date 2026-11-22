from contextlib import closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from warehouse import CorrectionBatch, MembershipInput, ObservationInput, OpeningBatch, ReplacementBatch, SourcePosition, SourceReference, ValueCorrection, apply_batch, apply_replacement, encode_edition, expire_input, open_day, open_district, open_source, read_balance, read_edition, read_replacement_origins, receive_edition, release_edition
from warehouse.intake.source_rows import list_expired_rows


def _rows(data, key, row_type):
    return tuple(row_type(**{**row, 'position': SourcePosition(**row['position'])}) for row in data[key])


def _district_result(audit):
    return {
        'edition': audit.edition,
        'entries': [[row.action, row.litres, row.source.edition, row.source.position.document,
                     row.source.position.row] for row in audit.entries],
        'rows': audit.row_count,
        'litres': audit.total_litres,
    }


def main() -> None:
    source_input = json.loads(Path(__file__).with_name('sample-day.json').read_text(encoding='utf-8'))
    day = source_input['day']
    districts = tuple(source_input['districts'])
    morning, fix = source_input['opening'], source_input['correction']
    opening = OpeningBatch(day, morning['request'], districts, _rows(morning, 'observations', ObservationInput),
                           _rows(morning, 'memberships', MembershipInput))
    correction = CorrectionBatch(day, fix['request'], _rows(fix, 'observations', ValueCorrection),
                                 _rows(fix, 'memberships', MembershipInput))
    with TemporaryDirectory() as directory:
        root = Path(directory)
        paths = {(district, number): root / f'{district}-{number}.json'
                 for district in districts for number in (1, 2)}
        with closing(open_source(root / 'source.sqlite')) as source:
            first = open_day(source, opening)
            second = apply_batch(source, correction)
            assert open_day(source, opening) == first
            for (district, number), path in paths.items():
                path.write_bytes(encode_edition(release_edition(source, day, district, number)))
            expired = expire_input(source, day, 2)
            remaining = len(list_expired_rows(source, day, 2))
        payloads = {key: path.read_bytes() for key, path in paths.items()}
        results = {}
        replayed = {}
        delivery = {'east': [1, 2], 'west': [2, 1, 2]}
        for district, order in delivery.items():
            local_path = root / f'{district}.sqlite'
            with closing(open_district(local_path, district)) as local:
                for number in order[:-1]:
                    received = receive_edition(local, payloads[district, number])
                    if district == 'west' and number == 2:
                        assert (received.status, received.frontier, received.receipt) == ('pending', 0, None)
                        assert received.completed == ()
                received = receive_edition(local, payloads[district, order[-1]])
                original = received.result
                assert original is not None
                if district == 'west':
                    assert received.completed == ()
            with closing(open_district(local_path, district)) as local:
                result = read_edition(local, day, district, 2)
                replay = receive_edition(local, payloads[district, 2])
                results[district] = _district_result(result)
                replayed[district] = original == result == replay.result and not replay.completed

        expected = {
            'initial_litres': {'east': 7, 'west': 0},
            'formal_litres': {'east': 0, 'west': 11},
            'removed_rows': 4,
            'remaining_source_rows': 0,
        }
        actual = {
            'initial_litres': {item.district: item.total_litres for item in first.projections},
            'formal_litres': {item.district: item.total_litres for item in second.projections},
            'removed_rows': expired.removed,
            'remaining_source_rows': remaining,
        }
        downstream_expected = {
            'east': {'edition': 2, 'entries': [['exit', 7, 1, 'morning.csv', 1]], 'rows': 0, 'litres': 0},
            'west': {'edition': 2, 'entries': [['enter', 11, 2, 'fix-observations.csv', 4]], 'rows': 1, 'litres': 11},
            'original_receipts_replayed': {'east': True, 'west': True},
        }
        downstream_actual = {**results, 'original_receipts_replayed': replayed}
        assert actual == expected, (actual, expected)
        assert downstream_actual == downstream_expected, (downstream_actual, downstream_expected)
        assert expired.blocked == ()
        print(f'{day} 离线复核：东区 {results["east"]["litres"]} L，西区 {results["west"]["litres"]} L，清理原始行 {expired.removed} 条。')
        evidence = {
            'case_id': 'V8', 'input': {**source_input, 'delivery': delivery, 'expire_through': 2},
            'expected': expected, 'actual': actual,
            'downstream_expected': downstream_expected, 'downstream_actual': downstream_actual,
            'code_paths': ['warehouse/ledger/projection.py', 'warehouse/edition/publish.py',
                           'warehouse/edition/retention.py', 'warehouse/district/advance.py', 'warehouse/audit/read.py'],
            'oracle': '东区手算 7−7=0，西区手算 0+11=11。晨报和更正各有一条观测行和一条站区行。退出指向晨报第 1 行，进入指向更正第 4 行。',
        }
        print('POINTWISE_SCENARIO ' + json.dumps(evidence, ensure_ascii=False, sort_keys=True))
        _replacement_demo(root)


def _replacement_demo(root: Path) -> None:
    orders = json.loads(Path(__file__).with_name('replacement-orders.json').read_text(encoding='utf-8'))
    day, station = orders['day'], orders['station']
    districts = tuple(orders['districts'])

    def position(value):
        return SourcePosition(value[0], value[1])

    def reference(value):
        return SourceReference(day, value[0], value[1], SourcePosition(value[2], value[3]))

    def observations(items):
        return tuple(ObservationInput(key, station, litres, position(row)) for key, litres, row in items)

    def order(spec, allocation_key='allocations'):
        allocations = spec[allocation_key] if allocation_key in spec else orders[allocation_key]
        return ReplacementBatch(
            day, spec['request_id'], station,
            tuple((key, reference(value)) for key, value in spec['retired']),
            observations(spec['new']),
            tuple((retired, new, reference(origin), litres)
                  for retired, new, origin, litres in allocations))

    def opening():
        source = orders['opening']
        return OpeningBatch(day, source['request_id'], districts, observations(source['observations']),
                            (MembershipInput(station, source['district'],
                                             position(source['membership_position'])),))

    with closing(open_source(root / 'replacement-source.sqlite')) as source:
        open_day(source, opening())
        first = apply_replacement(source, order(orders['orders'][0]))
        rejected = False
        try:
            apply_replacement(source, order(orders['orders'][1], 'bad_second_allocations'))
        except ValueError:
            rejected = True
        second = apply_replacement(source, order(orders['orders'][1]))
        payloads = {(district, number): encode_edition(release_edition(source, day, district, number))
                    for district in districts for number in (1, 2, 3)}
        expired = expire_input(source, day, 3)
    assert rejected, 'the mis-allocated order must be rejected'
    assert expired.blocked == () and expired.removed == 7, expired
    assert (first.edition, second.edition) == (2, 3)
    assert {projection.district: projection.total_litres for projection in second.projections} == {
        'east': 12, 'west': 0}

    with closing(open_source(root / 'replacement-alternate.sqlite')) as alternate:
        open_day(alternate, opening())
        apply_replacement(alternate, order(orders['orders'][0], 'alternate_first_allocations'))
        apply_replacement(alternate, order(orders['orders'][1], 'bad_second_allocations'))
        forged = encode_edition(release_edition(alternate, day, 'east', 3))

    east = root / 'replacement-east.sqlite'
    with closing(open_district(east, 'east')) as local:
        receive_edition(local, payloads['east', 1])
        late = receive_edition(local, payloads['east', 3])
        assert (late.status, late.frontier, late.completed) == ('pending', 1, ())
        finish = receive_edition(local, payloads['east', 2])
        completed = [(result.edition, result.total_litres) for result in finish.completed]
        rejected_by_district = False
        try:
            receive_edition(local, forged)
        except ValueError:
            rejected_by_district = True
        assert rejected_by_district, 'the district must reject the forged allocation'
        assert [(result.edition, result.total_litres) for result in finish.completed] == completed
    with closing(open_district(east, 'east')) as local:
        origins = {key: [(origin.edition, origin.position.document, origin.position.row, litres)
                         for origin, litres in read_replacement_origins(local, day, 'east', 3, key)]
                   for key in ('e', 'f')}
        receipt = read_edition(local, day, 'east', 2).receipt
        balance = read_balance(local, day, 'east')
        replay = receive_edition(local, payloads['east', 2])
    assert completed == [(2, 12), (3, 12)], completed
    assert origins['e'] == [(1, 'morning.csv', 1, 3)], origins
    assert origins['f'] == [(1, 'morning.csv', 1, 4), (1, 'morning.csv', 2, 5)], origins
    assert (balance.row_count, balance.total_litres) == (2, 12)
    assert replay.receipt == receipt and replay.completed == ()
    print(f'{day} 换表复核：第 2、3 刊东区各 {balance.total_litres} L，'
          f'错拨工单已拒收，清理原始行 {expired.removed} 条。')
    evidence = {
        'case_id': 'V9', 'input': orders,
        'expected': {'editions': [2, 3], 'east_litres': 12, 'out_of_order': [[2, 12], [3, 12]],
                     'bad_order': 'rejected', 'district_rejected_forged': True, 'origins': origins},
        'actual': {'editions': [first.edition, second.edition],
                   'east_litres': balance.total_litres, 'out_of_order': completed,
                   'bad_order': 'rejected' if rejected else 'accepted',
                   'district_rejected_forged': rejected_by_district, 'origins': origins},
        'code_paths': ['warehouse/ledger/replacement.py', 'warehouse/edition/publish.py',
                       'warehouse/district/origins.py', 'warehouse/district/advance.py'],
        'oracle': '第 2 刊 c=原 a 1 L+原 b 5 L、d=原 a 6 L；第 3 刊 e=原 a 3 L、f=原 a 4 L+原 b 5 L；'
                  '东区三刊均为 12 L。错拨单把 c 的 1 L 报成 3 L，源仓与区仓都拒收。',
    }
    print('POINTWISE_SCENARIO ' + json.dumps(evidence, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
