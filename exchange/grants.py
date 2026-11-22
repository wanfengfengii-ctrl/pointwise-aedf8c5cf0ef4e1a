from dataclasses import dataclass

from day.balance import require_available
from day.handoff import ACCEPT, record_handoff_event
from day.store import decode_int, encode_int
from exchange.documents import GrantDocument, dump_grant, load_grant, validate_grant_document


@dataclass(frozen=True, slots=True)
class AcceptedGrant:
    document: GrantDocument
    available: int


def ensure_grant_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS accepted_grants (
        grant_id TEXT PRIMARY KEY NOT NULL, station TEXT NOT NULL,
        business_day TEXT NOT NULL, document TEXT NOT NULL, available TEXT NOT NULL
    )''')
    db.execute('''CREATE INDEX IF NOT EXISTS accepted_grants_day
        ON accepted_grants (station, business_day, grant_id)''')


def _read_accepted(row):
    return AcceptedGrant(load_grant(row['document']), decode_int(row['available']))


def find_accepted_grant(db, grant_id):
    row = db.execute('''SELECT document, available FROM accepted_grants
        WHERE grant_id = ?''', (grant_id,)).fetchone()
    return None if row is None else _read_accepted(row)


def list_accepted_grants(db, station_day):
    rows = db.execute('''SELECT document, available FROM accepted_grants
        WHERE station = ? AND business_day = ? ORDER BY grant_id''', station_day.key)
    return tuple(_read_accepted(row) for row in rows)


def accept_grant(store, document):
    store.require_role('branch')
    validate_grant_document(document, hub_id=store.peer_id, branch_id=store.warehouse_id,
                            key=store.exchange_key)
    with store.write_transaction() as db:
        saved = find_accepted_grant(db, document.grant_id)
        if saved is not None:
            if saved.document != document:
                raise ValueError('同号接纳已绑定不同划拨凭据')
            return AcceptedGrant(saved.document, saved.document.rows)
        db.execute('INSERT INTO accepted_grants VALUES (?, ?, ?, ?, ?)', (
            document.grant_id, *document.station_day.key, dump_grant(document), encode_int(document.rows)
        ))
        record_handoff_event(db, document.station_day, ACCEPT, document.grant_id)
        return AcceptedGrant(document, document.rows)


def require_accepted_grant(db, station_day, grant_id):
    accepted = find_accepted_grant(db, grant_id)
    if accepted is None or accepted.document.station_day != station_day:
        raise ValueError('本站日无对应的已接纳划拨')
    return accepted


def consume_grant_rows(db, station_day, grant_id, count):
    accepted = require_accepted_grant(db, station_day, grant_id)
    available = require_available(accepted.available, count)
    db.execute('UPDATE accepted_grants SET available = ? WHERE grant_id = ?', (
        encode_int(available), grant_id
    ))
    return available
