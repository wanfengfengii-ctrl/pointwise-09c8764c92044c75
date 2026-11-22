from dataclasses import dataclass
from typing import Iterable

from warehouse.audit.entries import AuditEntry


@dataclass(frozen=True)
class EntryTotals:
    entered_litres: int
    exited_litres: int
    net_litres: int
    row_change: int


def summarize_entries(entries: Iterable[AuditEntry]) -> EntryTotals:
    entered = exited = row_change = 0
    for entry in entries:
        if entry.action == 'enter':
            entered += entry.litres
            row_change += 1
        elif entry.action == 'exit':
            exited += entry.litres
            row_change -= 1
        else:
            raise ValueError('unknown audit entry direction')
    return EntryTotals(entered, exited, entered - exited, row_change)
