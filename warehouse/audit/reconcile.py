from dataclasses import dataclass
import sqlite3

from warehouse.audit.entries import AuditEntry
from warehouse.audit.totals import EntryTotals, summarize_entries
from warehouse.district.postings import PostedEdition, load_posting


@dataclass(frozen=True)
class ReconciledMeasure:
    row_count: int
    total_litres: int
    totals: EntryTotals


def reconcile_posted_edition(
    db: sqlite3.Connection, posting: PostedEdition, entries: tuple[AuditEntry, ...]
) -> ReconciledMeasure:
    previous_rows = previous_litres = 0
    if posting.number > 1:
        previous = load_posting(db, posting.day, posting.district, posting.number - 1)
        if previous is None:
            raise RuntimeError('posted edition has no posted predecessor')
        previous_rows = previous.measure.row_count
        previous_litres = previous.measure.total_litres
    totals = summarize_entries(entries)
    expected = (previous_rows + totals.row_change, previous_litres + totals.net_litres)
    actual = (posting.measure.row_count, posting.measure.total_litres)
    if actual != expected or min(actual) < 0:
        raise RuntimeError(f'posting totals disagree with edition {posting.number} entries')
    return ReconciledMeasure(*actual, totals)
