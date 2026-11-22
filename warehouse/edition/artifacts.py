from dataclasses import asdict, dataclass
import json
import sqlite3

from warehouse.edition.changes import DistrictChange
from warehouse.intake.provenance import (
    SourceReference, reference_from_mapping, require_label, require_positive_integer,
)
from warehouse.intake.validate import validate_day


FROZEN_EDITION_SCHEMA = '''
CREATE TABLE IF NOT EXISTS frozen_editions (
    day TEXT NOT NULL,
    district TEXT NOT NULL,
    number INTEGER NOT NULL,
    payload BLOB NOT NULL,
    PRIMARY KEY (day, district, number)
)
'''


@dataclass(frozen=True)
class ReplacementMaterial:
    """The allocations actually applied by a replacement edition.

    ``allocations`` uses the same item shape as the declared order:
    ``(retired key, new key, origin source reference, litres)``.
    """

    station: str
    allocations: tuple[tuple[str, str, SourceReference, int], ...]

    def __post_init__(self) -> None:
        require_label(self.station, 'replacement station')
        if not isinstance(self.allocations, tuple):
            raise ValueError('replacement allocations must be a tuple')
        for item in self.allocations:
            if not isinstance(item, tuple) or len(item) != 4:
                raise ValueError('replacement allocation has an invalid shape')
            retired, new, origin, litres = item
            require_label(retired, 'allocation retired key')
            require_label(new, 'allocation new key')
            if not isinstance(origin, SourceReference):
                raise ValueError('replacement allocation requires a source reference')
            if type(litres) is not int or litres <= 0:
                raise ValueError('replacement allocation litres must be positive')


@dataclass(frozen=True)
class FrozenEdition:
    day: str
    district: str
    number: int
    changes: tuple[DistrictChange, ...]
    replacement: ReplacementMaterial | None = None
    qualifications: tuple[tuple[str, str, tuple[tuple[SourceReference, int], ...]], ...] = ()

    def __post_init__(self) -> None:
        validate_day(self.day)
        require_label(self.district, 'district')
        require_positive_integer(self.number, 'edition')
        if not isinstance(self.changes, tuple):
            raise ValueError('material changes must be a tuple')
        order = []
        by_observation: dict[str, DistrictChange] = {}
        for change in self.changes:
            if not isinstance(change, DistrictChange):
                raise ValueError('material contains an invalid entry')
            if change.source.day != self.day or change.source.edition > self.number:
                raise ValueError('entry provenance is outside the material edition')
            if change.action == 'exit' and change.source.edition >= self.number:
                raise ValueError('an exit must refer to a prior observation revision')
            by_observation[change.observation] = change
            order.append((change.observation, change.action == 'enter'))
        if order != sorted(set(order)):
            raise ValueError('material entries are repeated or out of order')
        if not isinstance(self.qualifications, tuple):
            raise ValueError('material qualifications must be a tuple')
        seen = set()
        for item in self.qualifications:
            if not isinstance(item, tuple) or len(item) != 3:
                raise ValueError('material qualification has an invalid shape')
            observation, district, origins = item
            require_label(observation, 'qualified observation')
            require_label(district, 'generation district')
            if observation in seen:
                raise ValueError('material qualification is repeated')
            seen.add(observation)
            change = by_observation.get(observation)
            if change is None or change.action != 'enter':
                raise ValueError(f'material qualification has no entering row: {observation}')
            if not isinstance(origins, tuple):
                raise ValueError('qualified origins must be a tuple')
            total = 0
            for source, litres in origins:
                if not isinstance(source, SourceReference):
                    raise ValueError('qualified origin requires a source reference')
                if type(litres) is not int or litres <= 0:
                    raise ValueError('qualified origin litres must be positive')
                total += litres
            if total != change.litres:
                raise ValueError(f'qualified origins do not match row litres: {observation}')
        if self.replacement is not None:
            self._check_allocations(by_observation)
        if self.replacement is not None and self.qualifications:
            allocated = {new for _, new, _, _ in self.replacement.allocations}
            if allocated & {observation for observation, _, _ in self.qualifications}:
                raise ValueError('a row cannot be both allocated and generation-qualified')

    def _check_allocations(self, by_observation: dict[str, DistrictChange]) -> None:
        if not isinstance(self.replacement, ReplacementMaterial):
            raise ValueError('material replacement must be a ReplacementMaterial')
        if self.replacement.station not in {change.station for change in self.changes}:
            raise ValueError('replacement station is absent from the material')
        for change in self.changes:
            if change.station != self.replacement.station:
                raise ValueError('replacement material covers more than one station')
        for retired, new, origin, litres in self.replacement.allocations:
            departure = by_observation.get(retired)
            if departure is None or departure.action != 'exit':
                raise ValueError(f'material allocation retires a missing row: {retired}')
            arrival = by_observation.get(new)
            if arrival is None or arrival.action != 'enter':
                raise ValueError(f'material allocation creates a missing row: {new}')
        totals: dict[str, int] = {}
        for retired, new, origin, litres in self.replacement.allocations:
            for key in (retired, new):
                totals[key] = totals.get(key, 0) + litres
        for observation, change in by_observation.items():
            if totals.get(observation, 0) != change.litres:
                raise ValueError(f'material allocation does not exhaust its share: {observation}')


def encode_edition(material: FrozenEdition) -> bytes:
    value = {
        'version': 1,
        'day': material.day,
        'district': material.district,
        'number': material.number,
        'changes': [asdict(change) for change in material.changes],
    }
    if material.replacement is not None:
        value['replacement'] = {
            'station': material.replacement.station,
            'allocations': [
                {'retired': retired, 'new': new, 'origin': asdict(origin), 'litres': litres}
                for retired, new, origin, litres in material.replacement.allocations
            ],
        }
    if material.qualifications:
        value['qualifications'] = [
            [observation, district,
             [[asdict(source), litres] for source, litres in origins]]
            for observation, district, origins in material.qualifications
        ]
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii')


def _decode_change(value: dict) -> DistrictChange:
    if not isinstance(value, dict) or set(value) != {'action', 'observation', 'station', 'litres', 'source'}:
        raise ValueError('invalid material entry fields')
    return DistrictChange(value['action'], value['observation'], value['station'],
                          value['litres'], reference_from_mapping(value['source']))


def _decode_replacement(value: object) -> ReplacementMaterial:
    if not isinstance(value, dict) or set(value) != {'station', 'allocations'}:
        raise ValueError('invalid replacement fields')
    allocations = value['allocations']
    if not isinstance(allocations, list):
        raise ValueError('replacement allocations must be a list')
    items = []
    for item in allocations:
        if not isinstance(item, dict) or set(item) != {'retired', 'new', 'origin', 'litres'}:
            raise ValueError('invalid replacement allocation fields')
        items.append((item['retired'], item['new'],
                      reference_from_mapping(item['origin']), item['litres']))
    return ReplacementMaterial(value['station'], tuple(items))


def _decode_qualifications(
    value: object,
) -> tuple[tuple[str, str, tuple[tuple[SourceReference, int], ...]], ...]:
    if not isinstance(value, list) or not value:
        raise ValueError('material qualifications must be a nonempty list')
    items = []
    for item in value:
        if not isinstance(item, list) or len(item) != 3 or not isinstance(item[2], list):
            raise ValueError('invalid material qualification')
        origins = []
        for pair in item[2]:
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValueError('invalid qualified origin')
            origins.append((reference_from_mapping(pair[0]), pair[1]))
        items.append((item[0], item[1], tuple(origins)))
    return tuple(items)


def decode_edition(payload: bytes) -> FrozenEdition:
    if not isinstance(payload, bytes):
        raise ValueError('material must be bytes')
    try:
        value = json.loads(payload.decode('ascii'))
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValueError('material has an invalid JSON encoding') from error
    allowed = {'version', 'day', 'district', 'number', 'changes'}
    if not isinstance(value, dict) or not allowed <= set(value) <= allowed | {
        'replacement', 'qualifications'
    }:
        raise ValueError('invalid material fields')
    if type(value['version']) is not int or value['version'] != 1 or not isinstance(value['changes'], list):
        raise ValueError('invalid material version or entries')
    material = FrozenEdition(
        value['day'], value['district'], value['number'],
        tuple(_decode_change(change) for change in value['changes']),
        _decode_replacement(value['replacement']) if 'replacement' in value else None,
        _decode_qualifications(value['qualifications']) if 'qualifications' in value else (),
    )
    if encode_edition(material) != payload:
        raise ValueError('material is not in its canonical encoding')
    return material


def same_material(left: FrozenEdition, right: FrozenEdition) -> bool:
    return left == right


def store_frozen_edition(db: sqlite3.Connection, material: FrozenEdition) -> None:
    db.execute(
        'INSERT INTO frozen_editions (day, district, number, payload) VALUES (?, ?, ?, ?)',
        (material.day, material.district, material.number, encode_edition(material)),
    )


def load_frozen_edition(
    db: sqlite3.Connection, day: str, district: str, number: int
) -> FrozenEdition | None:
    row = db.execute(
        'SELECT payload FROM frozen_editions WHERE day = ? AND district = ? AND number = ?',
        (day, district, number),
    ).fetchone()
    if row is None:
        return None
    material = decode_edition(row[0])
    if (material.day, material.district, material.number) != (day, district, number):
        raise ValueError('stored material scope does not match its formal key')
    return material
