import sqlite3

from warehouse.edition.artifacts import (
    FrozenEdition, ReplacementMaterial, load_frozen_edition, store_frozen_edition,
)
from warehouse.edition.changes import build_district_changes
from warehouse.intake.provenance import reference_from_text
from warehouse.intake.requests import lookup_request
from warehouse.ledger.evidence import load_revision_evidence, origin_sort_key
from warehouse.ledger.journal import districts_for_day, require_formal_edition
from warehouse.ledger.membership import memberships_at
from warehouse.ledger.projection import project_district


def district_allocations(
    db: sqlite3.Connection, day: str, district: str, number: int
) -> tuple[tuple[str, str, object, int], ...]:
    """Allocations generated in ``district`` by edition ``number``."""
    rows = db.execute(
        '''SELECT retired, new, origin, litres FROM replacement_allocations
           WHERE day = ? AND district = ? AND edition = ?''', (day, district, number),
    ).fetchall()
    items = [(retired, new, reference_from_text(origin), litres)
             for retired, new, origin, litres in rows]
    return tuple(sorted(items, key=lambda item: (origin_sort_key(item[2]), item[0], item[1])))


def district_qualifications(
    db: sqlite3.Connection, day: str, number: int
) -> tuple[tuple[str, str, tuple[tuple[object, int], ...]], ...]:
    """Generation qualification carried into an edition by moving revisions.

    A replacement revision keeps the generation district and the shares it
    actually received, even when it later moves district through an ordinary
    reassignment.  Revisions created by this edition are described by its own
    allocations instead, so they carry no qualification.
    """
    from warehouse.ledger.facts import observations_at

    facts = observations_at(db, day, number)
    items = []
    for key in sorted(facts):
        fact = facts[key]
        if fact.source.edition == number:
            continue
        evidence = load_revision_evidence(db, day, fact.source.edition, key)
        if evidence is None or not evidence.replacement:
            continue
        items.append((key, evidence.generation_district, evidence.origins))
    return tuple(items)


def release_edition(db: sqlite3.Connection, day: str, district: str, number: int) -> FrozenEdition:
    with db:
        db.execute('BEGIN IMMEDIATE')
        edition = require_formal_edition(db, day, number)
        if district not in districts_for_day(db, day):
            raise LookupError(f'unknown district {day} for {district}')
        existing = load_frozen_edition(db, day, district, number)
        if existing is not None:
            return existing
        before = ()
        if edition.previous:
            before = project_district(db, day, district, edition.previous).rows
        after = project_district(db, day, district, number).rows
        changes = build_district_changes(before, after)
        station = _replacement_station(db, edition, district, changes)
        allocations = _replacement_view(db, day, district, number, changes, station)
        replacement = (ReplacementMaterial(station, allocations)
                       if station is not None else None)
        enter_keys = {change.observation for change in changes if change.action == 'enter'}
        qualifications = tuple(
            item for item in district_qualifications(db, day, number)
            if item[0] in enter_keys)
        material = FrozenEdition(day, district, number, changes, replacement, qualifications)
        store_frozen_edition(db, material)
        return material


def _replacement_station(db, edition, district, changes):
    """The station whose replacement this district material carries, if any.

    A material only describes a replacement where the replacing station is
    currently resident; other districts see an ordinary change (or nothing).
    Zero-litre replacements carry no allocation records yet remain a
    replacement where the station lives.
    """
    if not changes:
        return None
    record = lookup_request(db, edition.day, edition.request_id)
    if record is None or record.operation != 'replacement':
        return None
    stations = {change.station for change in changes}
    if len(stations) != 1:
        return None
    station = next(iter(stations))
    membership = memberships_at(db, edition.day, edition.number).get(station)
    if membership is None or membership.district != district:
        return None
    return station


def _replacement_view(db, day, district, number, changes, station):
    retired = {change.observation for change in changes if change.action == 'exit'}
    entered = {change.observation for change in changes if change.action == 'enter'}
    allocations = []
    for retired_key, new_key, origin, litres in district_allocations(db, day, district, number):
        if retired_key in retired and new_key in entered:
            allocations.append((retired_key, new_key, origin, litres))
    if station is None:
        return tuple(allocations)
    totals: dict[str, int] = {}
    for retired_key, new_key, _, litres in allocations:
        for key in (retired_key, new_key):
            totals[key] = totals.get(key, 0) + litres
    for key in retired | entered:
        change = next(item for item in changes if item.observation == key)
        if totals.get(key, 0) != change.litres:
            raise RuntimeError(f'allocation view does not exhaust its share: {key}')
    return tuple(allocations)
