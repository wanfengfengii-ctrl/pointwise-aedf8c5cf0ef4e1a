import json
from dataclasses import dataclass

from day.calendar import check_name
from day.store import decode_int, encode_int
from intake.origins import RowOrigin, build_origin
from intake.units import normalize_litres


@dataclass(frozen=True, slots=True)
class ObservationRow:
    litres: int | None
    origin: RowOrigin


@dataclass(frozen=True, slots=True)
class RowMetrics:
    rows: int
    missing: int
    litres: int


def normalize_rows(source, entries):
    check_name(source, '报送来源')
    normalized = []
    for position, entry in enumerate(entries, 1):
        try:
            if not isinstance(entry, (tuple, list)) or len(entry) != 3:
                raise ValueError('每行须包含源行号、读值和单位')
            source_line, value, unit = entry
            origin = build_origin(source, source_line, position)
            normalized.append(ObservationRow(normalize_litres(value, unit), origin))
        except ValueError as error:
            raise ValueError(f'报送第 {position} 行：{error}') from error
    return tuple(normalized)


def measure_rows(rows):
    count = missing = litres = 0
    for row in rows:
        count += 1
        if row.litres is None:
            missing += 1
        else:
            litres += row.litres
    return RowMetrics(count, missing, litres)


def encode_rows(rows):
    return json.dumps([
        [row.origin.source, encode_int(row.origin.source_line),
         encode_int(row.origin.position), None if row.litres is None else encode_int(row.litres)]
        for row in rows
    ], separators=(',', ':'))


def decode_rows(text):
    return tuple(
        ObservationRow(None if value is None else decode_int(value),
                       RowOrigin(source, decode_int(source_line), decode_int(position)))
        for source, source_line, position, value in json.loads(text)
    )
