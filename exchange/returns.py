from dataclasses import dataclass

from day.balance import require_positive
from day.calendar import check_name, check_station_day
from day.handoff import RETURN, record_handoff_event
from day.store import decode_int, encode_int
from exchange.documents import ReturnDocument, dump_return, issue_return_document, load_return
from exchange.grants import consume_grant_rows, require_accepted_grant


@dataclass(frozen=True, slots=True)
class ReturnResult:
    document: ReturnDocument
    available_after: int


def ensure_return_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS branch_returns (
        request_id TEXT PRIMARY KEY NOT NULL,
        grant_id TEXT NOT NULL REFERENCES accepted_grants (grant_id),
        document TEXT NOT NULL, available_after TEXT NOT NULL
    )''')
    db.execute('CREATE INDEX IF NOT EXISTS branch_returns_grant ON branch_returns (grant_id)')


def _read_return(row):
    return ReturnResult(load_return(row['document']), decode_int(row['available_after']))


def find_return_result(db, request_id):
    row = db.execute('SELECT * FROM branch_returns WHERE request_id = ?', (request_id,)).fetchone()
    return None if row is None else _read_return(row)


def list_day_returns(db, station_day):
    rows = db.execute('''SELECT r.document, r.available_after FROM branch_returns r
        JOIN accepted_grants g ON r.grant_id = g.grant_id
        WHERE g.station = ? AND g.business_day = ? ORDER BY r.request_id''', station_day.key)
    return tuple(_read_return(row) for row in rows)


def _already_returned(db, grant_id):
    rows = db.execute('SELECT document FROM branch_returns WHERE grant_id = ?', (grant_id,))
    return sum(load_return(row['document']).returned_rows for row in rows)


def return_quota(store, station_day, grant_id, request_id, count, *, before_commit=None):
    store.require_role('branch')
    check_station_day(station_day)
    check_name(grant_id, '划拨号')
    check_name(request_id, '退额请求号')
    require_positive(count)
    with store.write_transaction() as db:
        result = find_return_result(db, request_id)
        if result is not None:
            document = result.document
            original = (document.grant.station_day, document.grant.grant_id, document.returned_rows)
            if original != (station_day, grant_id, count):
                raise ValueError('同号退额已使用不同参数')
            return result
        accepted = require_accepted_grant(db, station_day, grant_id)
        available = consume_grant_rows(db, station_day, grant_id, count)
        cumulative = _already_returned(db, grant_id) + count
        document = issue_return_document(store.exchange_key, accepted.document, request_id, count, cumulative)
        db.execute('INSERT INTO branch_returns VALUES (?, ?, ?, ?)', (
            request_id, grant_id, dump_return(document), encode_int(available)
        ))
        record_handoff_event(db, station_day, RETURN, request_id)
        if before_commit is not None:
            before_commit()
        return ReturnResult(document, available)
