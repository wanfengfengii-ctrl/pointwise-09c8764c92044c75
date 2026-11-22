from dataclasses import asdict, dataclass
import json
import sqlite3

from warehouse.edition.changes import DistrictChange
from warehouse.intake.provenance import reference_from_mapping, require_label, require_positive_integer
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
class FrozenEdition:
    day: str
    district: str
    number: int
    changes: tuple[DistrictChange, ...]

    def __post_init__(self) -> None:
        validate_day(self.day)
        require_label(self.district, 'district')
        require_positive_integer(self.number, 'edition')
        if not isinstance(self.changes, tuple):
            raise ValueError('material changes must be a tuple')
        order = []
        for change in self.changes:
            if not isinstance(change, DistrictChange):
                raise ValueError('material contains an invalid entry')
            if change.source.day != self.day or change.source.edition > self.number:
                raise ValueError('entry provenance is outside the material edition')
            if change.action == 'exit' and change.source.edition >= self.number:
                raise ValueError('an exit must refer to a prior observation revision')
            order.append((change.observation, change.action == 'enter'))
        if order != sorted(set(order)):
            raise ValueError('material entries are repeated or out of order')


def encode_edition(material: FrozenEdition) -> bytes:
    value = {
        'version': 1,
        'day': material.day,
        'district': material.district,
        'number': material.number,
        'changes': [asdict(change) for change in material.changes],
    }
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii')


def _decode_change(value: dict) -> DistrictChange:
    if not isinstance(value, dict) or set(value) != {'action', 'observation', 'station', 'litres', 'source'}:
        raise ValueError('invalid material entry fields')
    return DistrictChange(value['action'], value['observation'], value['station'],
                          value['litres'], reference_from_mapping(value['source']))


def decode_edition(payload: bytes) -> FrozenEdition:
    if not isinstance(payload, bytes):
        raise ValueError('material must be bytes')
    try:
        value = json.loads(payload.decode('ascii'))
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValueError('material has an invalid JSON encoding') from error
    if not isinstance(value, dict) or set(value) != {'version', 'day', 'district', 'number', 'changes'}:
        raise ValueError('invalid material fields')
    if type(value['version']) is not int or value['version'] != 1 or not isinstance(value['changes'], list):
        raise ValueError('invalid material version or entries')
    material = FrozenEdition(value['day'], value['district'], value['number'],
                             tuple(_decode_change(change) for change in value['changes']))
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
