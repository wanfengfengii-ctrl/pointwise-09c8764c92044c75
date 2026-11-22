from dataclasses import asdict, dataclass
import json


MAX_INTEGER = 2**63 - 1


def require_label(value: object, field: str) -> None:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f'{field} must be a nonempty, trimmed string')
    if '\x00' in value:
        raise ValueError(f'{field} contains a null character')
    try:
        value.encode('utf-8')
    except UnicodeError as error:
        raise ValueError(f'{field} is not valid Unicode text') from error


def require_positive_integer(value: object, field: str) -> None:
    if type(value) is not int or not 1 <= value <= MAX_INTEGER:
        raise ValueError(f'{field} must be a positive SQLite integer')


@dataclass(frozen=True)
class SourcePosition:
    document: str
    row: int

    def __post_init__(self) -> None:
        require_label(self.document, 'source document')
        require_positive_integer(self.row, 'source row')


@dataclass(frozen=True)
class SourceReference:
    day: str
    edition: int
    request_id: str
    position: SourcePosition

    def __post_init__(self) -> None:
        require_label(self.day, 'source day')
        require_positive_integer(self.edition, 'source edition')
        require_label(self.request_id, 'source request')
        if not isinstance(self.position, SourcePosition):
            raise ValueError('source reference requires a position')


def make_source_reference(
    day: str, edition: int, request_id: str, position: SourcePosition
) -> SourceReference:
    return SourceReference(day, edition, request_id, position)


def reference_from_mapping(value: dict) -> SourceReference:
    if not isinstance(value, dict) or set(value) != {'day', 'edition', 'request_id', 'position'}:
        raise ValueError('invalid source reference fields')
    position = value['position']
    if not isinstance(position, dict) or set(position) != {'document', 'row'}:
        raise ValueError('invalid source position fields')
    return SourceReference(
        value['day'], value['edition'], value['request_id'], SourcePosition(**position)
    )


def reference_to_text(reference: SourceReference) -> str:
    return json.dumps(asdict(reference), sort_keys=True, separators=(',', ':'))


def reference_from_text(text: str) -> SourceReference:
    return reference_from_mapping(json.loads(text))
