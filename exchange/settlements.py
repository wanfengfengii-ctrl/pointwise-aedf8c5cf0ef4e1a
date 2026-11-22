from dataclasses import dataclass

from day.allocations import get_issued_grant
from day.balance import credit_hub
from day.handoff import SETTLE, record_handoff_event
from day.store import decode_int, encode_int
from exchange.documents import ReturnDocument, dump_return, load_return, validate_return_document


@dataclass(frozen=True, slots=True)
class SettlementResult:
    document: ReturnDocument
    newly_restored: int
    restored_through: int
    available_after: int


def ensure_settlement_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS settlements (
        request_id TEXT PRIMARY KEY NOT NULL,
        grant_id TEXT NOT NULL REFERENCES issued_grants (grant_id), document TEXT NOT NULL,
        newly_restored TEXT NOT NULL, restored_through TEXT NOT NULL, available_after TEXT NOT NULL
    )''')
    db.execute('CREATE INDEX IF NOT EXISTS settlements_grant ON settlements (grant_id)')


def _read_settlement(row):
    return SettlementResult(load_return(row['document']), decode_int(row['newly_restored']),
                            decode_int(row['restored_through']), decode_int(row['available_after']))


def find_settlement(db, request_id):
    row = db.execute('SELECT * FROM settlements WHERE request_id = ?', (request_id,)).fetchone()
    return None if row is None else _read_settlement(row)


def list_day_settlements(db, station_day):
    rows = db.execute('''SELECT s.* FROM settlements s JOIN issued_grants g ON s.grant_id = g.grant_id
        WHERE g.station = ? AND g.business_day = ? ORDER BY s.request_id''', station_day.key)
    return tuple(_read_settlement(row) for row in rows)


def _restored_through(db, grant_id):
    rows = db.execute('SELECT restored_through FROM settlements WHERE grant_id = ?', (grant_id,))
    return max((decode_int(row['restored_through']) for row in rows), default=0)


def settle_return(store, document, *, before_commit=None):
    store.require_role('hub')
    validate_return_document(document, hub_id=store.warehouse_id, branch_id=store.peer_id,
                             key=store.exchange_key)
    binding = document.grant
    with store.write_transaction() as db:
        issued = get_issued_grant(db, binding.grant_id)
        if issued != binding:
            raise ValueError('退额凭据没有匹配的总仓原划拨')
        saved = find_settlement(db, document.request_id)
        if saved is not None:
            if saved.document != document:
                raise ValueError('同号退额已受理不同凭据')
            return saved
        previous = _restored_through(db, binding.grant_id)
        covered = max(previous, document.cumulative_rows)
        added = covered - previous
        available = credit_hub(db, binding.station_day, added)
        db.execute('INSERT INTO settlements VALUES (?, ?, ?, ?, ?, ?)', (
            document.request_id, binding.grant_id, dump_return(document),
            encode_int(added), encode_int(covered), encode_int(available)
        ))
        record_handoff_event(db, binding.station_day, SETTLE, document.request_id)
        if before_commit is not None:
            before_commit()
        return SettlementResult(document, added, covered, available)
