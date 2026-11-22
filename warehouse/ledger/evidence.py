from dataclasses import dataclass
import json
import sqlite3
from typing import Iterable

from warehouse.intake.provenance import SourceReference, reference_from_text, reference_to_text


REVISION_EVIDENCE_SCHEMA = '''
CREATE TABLE IF NOT EXISTS revision_evidence (
    day TEXT NOT NULL,
    edition INTEGER NOT NULL,
    observation TEXT NOT NULL,
    replacement INTEGER NOT NULL,
    generation_district TEXT,
    origins TEXT NOT NULL,
    PRIMARY KEY (day, edition, observation)
)
'''


@dataclass(frozen=True)
class RevisionEvidence:
    """Per-revision allocation evidence.

    An ordinary revision always owns its own reported source (a zero row owns an
    empty share).  A replacement revision owns the shares it actually received
    when it was generated, and may only be allocated further in the district
    that generated it.
    """

    replacement: bool
    generation_district: str | None
    origins: tuple[tuple[SourceReference, int], ...]


def origin_sort_key(origin: SourceReference) -> tuple:
    return (origin.day, origin.edition, origin.request_id,
            origin.position.document, origin.position.row)


def sorted_origins(origins: Iterable[tuple[SourceReference, int]]) -> tuple[tuple[SourceReference, int], ...]:
    return tuple(sorted(origins, key=lambda item: origin_sort_key(item[0])))


def origins_for_ordinary(source: SourceReference, litres: int) -> tuple[tuple[SourceReference, int], ...]:
    return () if litres == 0 else ((source, litres),)


def encode_origins(origins: Iterable[tuple[SourceReference, int]]) -> str:
    return json.dumps([[reference_to_text(source), litres] for source, litres in origins],
                      separators=(',', ':'))


def decode_origins(text: str) -> tuple[tuple[SourceReference, int], ...]:
    try:
        value = json.loads(text)
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError('stored origins are not valid JSON') from error
    if not isinstance(value, list):
        raise ValueError('stored origins are not a list')
    result = []
    for item in value:
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError('stored origin entry is invalid')
        source_text, litres = item
        if type(litres) is not int or litres <= 0:
            raise ValueError('stored origin litres must be a positive integer')
        result.append((reference_from_text(source_text), litres))
    return tuple(result)


def record_revision_evidence(
    db: sqlite3.Connection,
    day: str,
    edition: int,
    observation: str,
    evidence: RevisionEvidence,
) -> None:
    db.execute(
        '''INSERT INTO revision_evidence
           (day, edition, observation, replacement, generation_district, origins)
           VALUES (?, ?, ?, ?, ?, ?)''',
        (day, edition, observation, int(evidence.replacement),
         evidence.generation_district, encode_origins(evidence.origins)),
    )


def load_revision_evidence(
    db: sqlite3.Connection, day: str, edition: int, observation: str
) -> RevisionEvidence | None:
    row = db.execute(
        '''SELECT replacement, generation_district, origins FROM revision_evidence
           WHERE day = ? AND edition = ? AND observation = ?''',
        (day, edition, observation),
    ).fetchone()
    if row is None:
        return None
    replacement, district, text = row
    evidence = RevisionEvidence(bool(replacement), district, decode_origins(text))
    if evidence.replacement and not evidence.generation_district:
        raise RuntimeError('replacement revision is missing its generation district')
    return evidence


def record_ordinary_evidence(
    db: sqlite3.Connection,
    day: str,
    edition: int,
    observation: str,
    source: SourceReference,
    litres: int,
) -> None:
    record_revision_evidence(
        db, day, edition, observation,
        RevisionEvidence(False, None, origins_for_ordinary(source, litres)),
    )
