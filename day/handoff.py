"""站日错峰交班的启用状态与已提交事件序。"""
from day.calendar import check_station_day

OPEN = 'open'
GRANT = 'grant'
ACCEPT = 'accept'
RELEASE = 'release'
RETURN = 'return'
SETTLE = 'settle'

HUB_EVENT_KINDS = (OPEN, GRANT, RELEASE, SETTLE)
BRANCH_EVENT_KINDS = (ACCEPT, RELEASE, RETURN)


def ensure_handoff_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS handoff_days (
        station TEXT NOT NULL, business_day TEXT NOT NULL,
        PRIMARY KEY (station, business_day)
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS handoff_events (
        station TEXT NOT NULL, business_day TEXT NOT NULL,
        position INTEGER NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL,
        PRIMARY KEY (station, business_day, position)
    )''')


def handoff_enabled(db, station_day):
    row = db.execute('SELECT 1 FROM handoff_days WHERE station = ? AND business_day = ?',
                     station_day.key).fetchone()
    return row is not None


def _formal_change_statements(role):
    day_filter = 'station = ? AND business_day = ?'
    statements = [f'SELECT 1 FROM releases WHERE {day_filter} LIMIT 1']
    if role == 'hub':
        statements.append(f'SELECT 1 FROM budgets WHERE {day_filter} LIMIT 1')
        statements.append(f'SELECT 1 FROM issued_grants WHERE {day_filter} LIMIT 1')
        statements.append('''SELECT 1 FROM settlements s
            JOIN issued_grants g ON s.grant_id = g.grant_id
            WHERE g.station = ? AND g.business_day = ? LIMIT 1''')
    else:
        statements.append(f'SELECT 1 FROM accepted_grants WHERE {day_filter} LIMIT 1')
        statements.append('''SELECT 1 FROM branch_returns r
            JOIN accepted_grants g ON r.grant_id = g.grant_id
            WHERE g.station = ? AND g.business_day = ? LIMIT 1''')
    return statements


def enable_handoff_day(db, role, station_day):
    check_station_day(station_day)
    if handoff_enabled(db, station_day):
        return
    for statement in _formal_change_statements(role):
        if db.execute(statement, station_day.key).fetchone() is not None:
            raise ValueError('本站日已有正式变更，无法启用交班')
    db.execute('INSERT INTO handoff_days VALUES (?, ?)', station_day.key)


def record_handoff_event(db, station_day, kind, ref):
    # 与业务写同事务：失败回滚不入序，同参重投不走新提交路径也不入序。
    if not handoff_enabled(db, station_day):
        return
    position = db.execute('''SELECT COUNT(*) FROM handoff_events
        WHERE station = ? AND business_day = ?''', station_day.key).fetchone()[0] + 1
    db.execute('INSERT INTO handoff_events VALUES (?, ?, ?, ?, ?)',
               (*station_day.key, position, kind, ref))


def list_handoff_events(db, station_day):
    rows = db.execute('''SELECT position, kind, ref FROM handoff_events
        WHERE station = ? AND business_day = ? ORDER BY position''', station_day.key)
    return tuple((row['position'], row['kind'], row['ref']) for row in rows)
