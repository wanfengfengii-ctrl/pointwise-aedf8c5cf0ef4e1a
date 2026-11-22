from day.balance import debit_hub, require_positive
from day.calendar import check_name, check_station_day
from exchange.documents import dump_grant, issue_grant_document, load_grant


def ensure_allocation_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS issued_grants (
        grant_id TEXT PRIMARY KEY NOT NULL,
        station TEXT NOT NULL, business_day TEXT NOT NULL, document TEXT NOT NULL,
        FOREIGN KEY (station, business_day) REFERENCES budgets (station, business_day)
    )''')
    db.execute('''CREATE INDEX IF NOT EXISTS issued_grants_day
        ON issued_grants (station, business_day, grant_id)''')


def get_issued_grant(db, grant_id):
    saved = db.execute('SELECT document FROM issued_grants WHERE grant_id = ?', (grant_id,)).fetchone()
    return None if saved is None else load_grant(saved['document'])


def list_issued_grants(db, station_day):
    results = db.execute('''SELECT document FROM issued_grants
        WHERE station = ? AND business_day = ? ORDER BY grant_id''', station_day.key)
    return tuple(load_grant(row['document']) for row in results)


def grant(store, station_day, grant_id, branch_id, count):
    store.require_role('hub')
    check_station_day(station_day)
    check_name(grant_id, '划拨号')
    check_name(branch_id, '分仓')
    require_positive(count)
    if branch_id != store.peer_id:
        raise ValueError('划拨须指定已配置的分仓')
    document = issue_grant_document(
        store.exchange_key, station_day, grant_id, store.warehouse_id, branch_id, count
    )
    with store.write_transaction() as db:
        saved = get_issued_grant(db, grant_id)
        if saved is not None:
            if saved != document:
                raise ValueError('同号划拨已使用不同参数')
            return saved
        debit_hub(db, station_day, count)
        db.execute('INSERT INTO issued_grants VALUES (?, ?, ?, ?)', (
            grant_id, *station_day.key, dump_grant(document)
        ))
        return document
