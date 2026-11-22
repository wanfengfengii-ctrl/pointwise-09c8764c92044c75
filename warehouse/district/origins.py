from dataclasses import dataclass
import sqlite3

from warehouse.edition.artifacts import FrozenEdition
from warehouse.intake.provenance import SourceReference
from warehouse.ledger.evidence import decode_origins, encode_origins, sorted_origins


ORIGIN_SCHEMAS = (
    '''CREATE TABLE IF NOT EXISTS district_replacement_origins (
           day TEXT NOT NULL,
           district TEXT NOT NULL,
           edition INTEGER NOT NULL,
           observation TEXT NOT NULL,
           origins TEXT NOT NULL,
           PRIMARY KEY (day, district, edition, observation)
       )''',
    '''CREATE TABLE IF NOT EXISTS district_generation_qualifications (
           day TEXT NOT NULL,
           district TEXT NOT NULL,
           edition INTEGER NOT NULL,
           observation TEXT NOT NULL,
           generation_district TEXT NOT NULL,
           origins TEXT NOT NULL,
           PRIMARY KEY (day, district, edition, observation)
       )''',
)


@dataclass(frozen=True)
class EnterEvidence:
    origins: tuple[tuple[SourceReference, int], ...]
    allocatable: bool


def _declared_incoming(material: FrozenEdition) -> dict[str, dict[SourceReference, int]]:
    shares: dict[str, dict[SourceReference, int]] = {}
    if material.replacement is not None:
        for _, new, origin, litres in material.replacement.allocations:
            bucket = shares.setdefault(new, {})
            bucket[origin] = bucket.get(origin, 0) + litres
    for observation, _, origins in material.qualifications:
        bucket = shares.setdefault(observation, {})
        for origin, litres in origins:
            bucket[origin] = bucket.get(origin, 0) + litres
    return shares


def _declared_retired(material: FrozenEdition) -> dict[str, dict[SourceReference, int]]:
    shares: dict[str, dict[SourceReference, int]] = {}
    if material.replacement is None:
        return shares
    for retired, _, origin, litres in material.replacement.allocations:
        bucket = shares.setdefault(retired, {})
        bucket[origin] = bucket.get(origin, 0) + litres
    return shares


def verify_and_derive(
    db: sqlite3.Connection, material: FrozenEdition, balance: dict
) -> dict[str, EnterEvidence]:
    """Independently check a material against local postings and derive its entries."""
    entering = {change.observation: change for change in material.changes if change.action == 'enter'}
    exiting = {change.observation: change for change in material.changes if change.action == 'exit'}
    enters = set(entering)
    exits = set(exiting)
    qualifications = {observation: (generation, origins)
                      for observation, generation, origins in material.qualifications}
    if not set(qualifications) <= enters:
        raise ValueError('qualification refers to a row that does not enter')
    incoming = _declared_incoming(material)
    retired_declared = _declared_retired(material)
    evidence: dict[str, EnterEvidence] = {}

    for key in sorted(exits):
        row = balance.get(key)
        if row is None:
            raise ValueError(f'exit has no current balance row: {key}')
        change = exiting[key]
        if (row.station, row.litres, row.source) != (change.station, change.litres, change.source):
            raise ValueError(f'exit does not match the current balance row: {key}')
        if material.replacement is not None:
            if not row.allocatable:
                raise ValueError(f'exit row is not allocatable in this district: {key}')
            if key in retired_declared and {
                    origin: litres for origin, litres in retired_declared[key].items()
            } != dict(row.origins):
                raise ValueError(f'exit shares do not match the local ledger: {key}')

    for key in sorted(enters):
        change = entering[key]
        declared = incoming.get(key)
        qualification = qualifications.get(key)
        if qualification is not None:
            generation, origins = qualification
            if declared is not None and dict(declared) != dict(origins):
                raise ValueError(f'qualified origins disagree with allocations: {key}')
            evidence[key] = EnterEvidence(origins, generation == material.district)
        elif declared is not None:
            evidence[key] = EnterEvidence(sorted_origins(declared.items()), True)
        else:
            origins = () if change.litres == 0 else ((change.source, change.litres),)
            evidence[key] = EnterEvidence(origins, True)

    for key, declared in incoming.items():
        if key not in enters:
            raise ValueError(f'allocation creates a row that does not enter: {key}')
        total = sum(declared.values())
        if total != entering[key].litres:
            raise ValueError(f'new row does not receive its full share: {key}')
    for key, declared in retired_declared.items():
        if key not in exits:
            raise ValueError(f'allocation retires a row that does not exit: {key}')
    if material.replacement is not None:
        if material.replacement.station not in {change.station for change in material.changes}:
            raise ValueError('replacement station is absent from the material')
        for key in exits:
            if exiting[key].litres and key not in retired_declared:
                raise ValueError(f'replacement material omits the share of {key}')
        for key in enters:
            if entering[key].litres and key not in incoming and key not in qualifications:
                raise ValueError(f'replacement material omits the share of {key}')
    return evidence


def record_entry_evidence(
    db: sqlite3.Connection, material: FrozenEdition, evidence: dict[str, EnterEvidence]
) -> None:
    if material.replacement is not None:
        for change in material.changes:
            if change.action != 'enter' or change.source.edition != material.number:
                continue
            item = evidence[change.observation]
            db.execute(
                '''INSERT INTO district_replacement_origins
                   (day, district, edition, observation, origins) VALUES (?, ?, ?, ?, ?)''',
                (material.day, material.district, material.number, change.observation,
                 encode_origins(item.origins)),
            )
    for observation, generation, origins in material.qualifications:
        db.execute(
            '''INSERT INTO district_generation_qualifications
               (day, district, edition, observation, generation_district, origins)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (material.day, material.district, material.number, observation, generation,
             encode_origins(origins)),
        )


def read_replacement_origins(
    db: sqlite3.Connection, day: str, district: str, number: int, observation: str
) -> tuple[tuple[SourceReference, int], ...]:
    from warehouse.district.postings import load_posting
    from warehouse.edition.artifacts import decode_edition
    from warehouse.intake.provenance import require_label
    from warehouse.intake.validate import validate_day
    from warehouse.ledger.journal import store_owner

    validate_day(day)
    require_label(district, 'district')
    require_label(observation, 'observation')
    if type(number) is not int or number <= 0:
        raise ValueError('edition must be a positive integer')
    if store_owner(db) != ('district', district):
        raise LookupError(f'unknown district in this warehouse: {district}')
    posting = load_posting(db, day, district, number)
    if posting is None:
        raise LookupError(f'edition has not been posted: {day}/{district}/{number}')
    material = decode_edition(posting.payload)
    if material.replacement is None:
        raise LookupError(f'edition {number} is not a replacement edition')
    if not any(change.observation == observation and change.action == 'enter'
               and change.source.edition == number for change in material.changes):
        raise LookupError(f'observation {observation} was not created by edition {number}')
    row = db.execute(
        '''SELECT origins FROM district_replacement_origins
           WHERE day = ? AND district = ? AND edition = ? AND observation = ?''',
        (day, district, number, observation),
    ).fetchone()
    if row is None:
        raise LookupError(f'observation {observation} was not created by edition {number}')
    return decode_origins(row[0])
