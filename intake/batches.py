import json
from dataclasses import dataclass

from day.calendar import StationDay, check_name, check_station_day, parse_station_day
from day.store import encode_int
from intake.rows import ObservationRow, decode_rows, encode_rows, normalize_rows


@dataclass(frozen=True, slots=True)
class StagedBatch:
    station_day: StationDay
    batch_id: str
    source: str
    rows: tuple[ObservationRow, ...]


def ensure_batch_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS staged_batches (
        batch_id TEXT PRIMARY KEY NOT NULL,
        station TEXT NOT NULL, business_day TEXT NOT NULL, source TEXT NOT NULL,
        raw_entries TEXT NOT NULL, observation_rows TEXT NOT NULL
    )''')


def _find_batch_data(db, batch_id):
    return db.execute('SELECT * FROM staged_batches WHERE batch_id = ?', (batch_id,)).fetchone()


def _read_batch(row):
    return StagedBatch(parse_station_day(row['station'], row['business_day']),
                       row['batch_id'], row['source'], decode_rows(row['observation_rows']))


def find_staged_batch(db, batch_id):
    row = _find_batch_data(db, batch_id)
    return None if row is None else _read_batch(row)


def stage(store, station_day, batch_id, source, entries):
    check_station_day(station_day)
    check_name(batch_id, '报送号')
    try:
        raw = tuple(tuple(row) if isinstance(row, (tuple, list)) else row for row in entries)
    except TypeError as error:
        raise ValueError('报送须为有限的有序行序列') from error
    rows = normalize_rows(source, raw)
    original = json.dumps([
        [encode_int(source_line), None if value is None else encode_int(value), unit]
        for source_line, value, unit in raw
    ], separators=(',', ':'))
    with store.write_transaction() as db:
        saved = _find_batch_data(db, batch_id)
        if saved is not None:
            previous = _read_batch(saved)
            if (previous.station_day, previous.source, saved['raw_entries']) != (station_day, source, original):
                raise ValueError('同号报送已使用不同参数')
            return previous
        db.execute('INSERT INTO staged_batches VALUES (?, ?, ?, ?, ?, ?)', (
            batch_id, *station_day.key, source, original, encode_rows(rows)
        ))
        return StagedBatch(station_day, batch_id, source, rows)
