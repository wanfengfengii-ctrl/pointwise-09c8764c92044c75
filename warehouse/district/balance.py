from dataclasses import dataclass
import sqlite3

from warehouse.edition.changes import DistrictChange
from warehouse.intake.provenance import reference_to_text
from warehouse.intake.validate import validate_day
from warehouse.ledger.journal import store_owner


BALANCE_SCHEMA = '''
CREATE TABLE IF NOT EXISTS district_balance (
    day TEXT NOT NULL,
    district TEXT NOT NULL,
    observation TEXT NOT NULL,
    station TEXT NOT NULL,
    litres INTEGER NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (day, district, observation)
)
'''


@dataclass(frozen=True)
class BalanceMeasure:
    row_count: int
    total_litres: int


def require_local_district(db: sqlite3.Connection, district: str) -> None:
    if store_owner(db) != ('district', district):
        raise LookupError(f'unknown district in this warehouse: {district}')


def read_balance(db: sqlite3.Connection, day: str, district: str) -> BalanceMeasure:
    validate_day(day)
    require_local_district(db, district)
    count = total = 0
    for (litres,) in db.execute('SELECT litres FROM district_balance WHERE day = ? AND district = ?',
                               (day, district)):
        count += 1
        total += litres
    return BalanceMeasure(count, total)


def apply_district_changes(
    db: sqlite3.Connection, day: str, district: str, changes: tuple[DistrictChange, ...]
) -> BalanceMeasure:
    for change in changes:
        arguments = (day, district, change.observation, change.station,
                     change.litres, reference_to_text(change.source))
        if change.action == 'exit':
            result = db.execute(
                '''DELETE FROM district_balance WHERE day = ? AND district = ? AND observation = ?
                   AND station = ? AND litres = ? AND source = ?''', arguments,
            )
            if result.rowcount != 1:
                raise ValueError(f'exit has no exact prior row: {change.observation}')
        else:
            result = db.execute(
                '''INSERT INTO district_balance (day, district, observation, station, litres, source)
                   VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(day, district, observation) DO NOTHING''',
                arguments,
            )
            if result.rowcount != 1:
                raise ValueError(f'entry would replace an existing row: {change.observation}')
    return read_balance(db, day, district)
