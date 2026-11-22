import json
import sqlite3
from dataclasses import asdict
from typing import Callable

from warehouse.intake.provenance import SourceReference, make_source_reference, reference_to_text
from warehouse.intake.replacement import (
    ReplacementBatch, allocation_shares, received_shares, validate_replacement_scope,
)
from warehouse.intake.requests import (
    RequestRecord, lookup_request, record_request, request_parameters, verify_request_parameters,
)
from warehouse.intake.source_rows import SourceRow, store_source_rows
from warehouse.intake.validate import validate_request_scope
from warehouse.ledger.evidence import (
    RevisionEvidence, load_revision_evidence, origins_for_ordinary,
    record_revision_evidence, sorted_origins,
)
from warehouse.ledger.facts import (
    ObservationRetirement, ObservationRevision, observations_at,
    record_observation_revisions, record_observation_retirements,
)
from warehouse.ledger.journal import FormalEdition, districts_for_day, list_formal_editions, record_formal_edition
from warehouse.ledger.membership import memberships_at
from warehouse.ledger.projection import project_day


REPLACEMENT_ALLOCATION_SCHEMA = '''
CREATE TABLE IF NOT EXISTS replacement_allocations (
    day TEXT NOT NULL,
    edition INTEGER NOT NULL,
    district TEXT NOT NULL,
    request_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    retired TEXT NOT NULL,
    new TEXT NOT NULL,
    origin TEXT NOT NULL,
    litres INTEGER NOT NULL,
    PRIMARY KEY (day, edition, ordinal)
)
'''


def apply_replacement(
    db: sqlite3.Connection, batch: ReplacementBatch, *,
    before_commit: Callable[[], None] | None = None,
):
    if not isinstance(batch, ReplacementBatch):
        raise ValueError('replacement requires a ReplacementBatch')
    validate_request_scope(batch.day, batch.request_id)
    with db:
        db.execute('BEGIN IMMEDIATE')
        editions = list_formal_editions(db, batch.day)
        if not editions:
            raise LookupError(f'day must be open before replacement: {batch.day}')
        parameters = request_parameters(batch)
        record = lookup_request(db, batch.day, batch.request_id)
        if record is not None:
            verify_request_parameters(record, 'replacement', parameters)
            return _result(db, record.day, record.edition)
        validate_replacement_scope(batch)
        current = editions[-1].number
        facts = observations_at(db, batch.day, current)
        memberships = memberships_at(db, batch.day, current)
        districts = districts_for_day(db, batch.day)
        if batch.station not in memberships:
            raise ValueError(f'unknown station: {batch.station}')
        generation = memberships[batch.station].district
        if generation not in districts:
            raise ValueError(f'unknown district: {generation}')
        used = _used_keys(db, batch.day)
        shares = allocation_shares(batch)
        incoming = received_shares(batch)
        _check_retired(db, batch, facts, generation, shares)
        for row in batch.new:
            if row.station != batch.station:
                raise ValueError(f'new observation {row.observation} belongs to another station')
            if row.observation in used:
                raise ValueError(f'observation key is already used today: {row.observation}')
            if row.litres != sum(litres for _, litres in incoming.get(row.observation, ())):
                raise ValueError(f'new row {row.observation} does not receive its full share')
        edition = current + 1

        def source_at(position):
            return make_source_reference(batch.day, edition, batch.request_id, position)

        facts_new = tuple(ObservationRevision(row.observation, batch.station, row.litres,
                                              source_at(row.position)) for row in batch.new)
        store_source_rows(db, _source_rows(batch, edition))
        record_observation_revisions(db, facts_new)
        record_observation_retirements(db, edition, tuple(
            ObservationRetirement(key, facts[key].station, facts[key].source)
            for key, _ in batch.retired))
        for row in facts_new:
            origins = _normalized(incoming.get(row.observation, ()))
            record_revision_evidence(db, batch.day, edition, row.observation, RevisionEvidence(
                True, generation, origins if row.litres else ()))
        for ordinal, item in enumerate(batch.allocations, 1):
            retired_key, new_key, origin, litres = item
            db.execute(
                '''INSERT INTO replacement_allocations
                   (day, edition, district, request_id, ordinal, retired, new, origin, litres)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (batch.day, edition, generation, batch.request_id, ordinal, retired_key,
                 new_key, reference_to_text(origin), litres),
            )
        record_formal_edition(db, FormalEdition(
            batch.day, edition, batch.request_id, len(facts_new), 0, len(batch.retired)))
        record_request(db, RequestRecord(batch.day, batch.request_id, 'replacement', parameters, edition))
        result = _result(db, batch.day, edition)
        if before_commit is not None:
            before_commit()
        return result


def _check_retired(db, batch, facts, generation: str, shares) -> None:
    for key, reference in batch.retired:
        fact = facts.get(key)
        if fact is None:
            raise ValueError(f'retired row is not on the previous edition: {key}')
        if fact.station != batch.station:
            raise ValueError(f'retired row belongs to another station: {key}')
        if fact.source != reference:
            raise ValueError(f'retired row does not match its reported provenance: {key}')
        evidence = load_revision_evidence(db, batch.day, fact.source.edition, key)
        expected = _expected_share(fact, evidence)
        if evidence is not None and evidence.replacement and evidence.generation_district != generation:
            raise ValueError(
                f'replacement row {key} may only be allocated in its generation district')
        declared = _normalized(shares.get(key, ()))
        if declared != expected:
            raise ValueError(f'retired row {key} is not drawn from its original origins')


def _expected_share(fact: ObservationRevision, evidence: RevisionEvidence | None):
    if evidence is None:
        return sorted_origins(origins_for_ordinary(fact.source, fact.litres))
    return sorted_origins(evidence.origins)


def _result(db: sqlite3.Connection, day: str, number: int):
    from warehouse.ledger.commit import CommitResult
    return CommitResult(day, number, project_day(db, day, number))


def _used_keys(db: sqlite3.Connection, day: str) -> set[str]:
    rows = db.execute(
        '''SELECT observation FROM observation_revisions WHERE day = ?
           UNION SELECT observation FROM observation_retirements WHERE day = ?''',
        (day, day),
    )
    return {observation for (observation,) in rows}


def _normalized(origins) -> tuple[tuple[SourceReference, int], ...]:
    merged: dict[SourceReference, int] = {}
    for source, litres in origins:
        if type(litres) is not int or litres <= 0:
            raise ValueError('allocation share must be a positive integer')
        merged[source] = merged.get(source, 0) + litres
    return sorted_origins(merged.items())


def _source_rows(batch, edition: int) -> tuple[SourceRow, ...]:
    rows = []
    for item in batch.new:
        raw = json.dumps(asdict(item), sort_keys=True, separators=(',', ':'))
        rows.append(SourceRow(batch.day, edition, batch.request_id, len(rows) + 1,
                              'observation', item.position, raw))
    return tuple(rows)
