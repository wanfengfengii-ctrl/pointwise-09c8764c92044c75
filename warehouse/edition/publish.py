import sqlite3

from warehouse.edition.artifacts import FrozenEdition, load_frozen_edition, store_frozen_edition
from warehouse.edition.changes import build_district_changes
from warehouse.ledger.journal import districts_for_day, require_formal_edition
from warehouse.ledger.projection import project_district


def release_edition(db: sqlite3.Connection, day: str, district: str, number: int) -> FrozenEdition:
    with db:
        db.execute('BEGIN IMMEDIATE')
        edition = require_formal_edition(db, day, number)
        if district not in districts_for_day(db, day):
            raise LookupError(f'unknown district {district} for {day}')
        existing = load_frozen_edition(db, day, district, number)
        if existing is not None:
            return existing
        before = ()
        if edition.previous:
            before = project_district(db, day, district, edition.previous).rows
        after = project_district(db, day, district, number).rows
        material = FrozenEdition(day, district, number, build_district_changes(before, after))
        store_frozen_edition(db, material)
        return material
