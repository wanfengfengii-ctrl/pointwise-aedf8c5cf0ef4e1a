from dataclasses import dataclass

from day.calendar import parse_station_day
from day.store import decode_int, encode_int
from intake.rows import ObservationRow, decode_rows, encode_rows
from publish.usage import QuotaReference


@dataclass(frozen=True, slots=True)
class ReleaseRecord:
    release_id: str
    batch_id: str
    quota: QuotaReference
    charged_rows: int
    rows: tuple[ObservationRow, ...]


def ensure_release_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS releases (
        release_id TEXT PRIMARY KEY NOT NULL,
        batch_id TEXT UNIQUE NOT NULL REFERENCES staged_batches (batch_id),
        station TEXT NOT NULL, business_day TEXT NOT NULL,
        warehouse_id TEXT NOT NULL, hub_id TEXT NOT NULL, grant_id TEXT,
        charged_rows TEXT NOT NULL, observation_rows TEXT NOT NULL
    )''')
    db.execute('''CREATE INDEX IF NOT EXISTS releases_day
        ON releases (station, business_day, release_id)''')


def store_release(db, record):
    quota = record.quota
    db.execute('INSERT INTO releases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', (
        record.release_id, record.batch_id, *quota.station_day.key,
        quota.warehouse_id, quota.hub_id, quota.grant_id,
        encode_int(record.charged_rows), encode_rows(record.rows)
    ))


def _read_release(row):
    quota = QuotaReference(parse_station_day(row['station'], row['business_day']),
                           row['warehouse_id'], row['hub_id'], row['grant_id'])
    return ReleaseRecord(row['release_id'], row['batch_id'], quota,
                         decode_int(row['charged_rows']), decode_rows(row['observation_rows']))


def find_release(db, release_id):
    row = db.execute('SELECT * FROM releases WHERE release_id = ?', (release_id,)).fetchone()
    return None if row is None else _read_release(row)


def find_release_for_batch(db, batch_id):
    row = db.execute('SELECT * FROM releases WHERE batch_id = ?', (batch_id,)).fetchone()
    return None if row is None else _read_release(row)


def list_day_releases(db, station_day):
    rows = db.execute('''SELECT * FROM releases
        WHERE station = ? AND business_day = ? ORDER BY release_id''', station_day.key)
    return tuple(_read_release(row) for row in rows)
