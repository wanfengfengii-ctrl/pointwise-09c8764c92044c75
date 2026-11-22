from contextlib import closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from warehouse import CorrectionBatch, MembershipInput, ObservationInput, OpeningBatch, SourcePosition, ValueCorrection, apply_batch, encode_edition, expire_input, open_day, open_district, open_source, read_edition, receive_edition, release_edition
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


if __name__ == '__main__':
    main()
