from dataclasses import dataclass

from day.calendar import StationDay, check_station_day
from day.store import decode_int, encode_int


@dataclass(frozen=True, slots=True)
class DayBudget:
    station_day: StationDay
    limit: int
    available: int


def ensure_budget_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS budgets (
        station TEXT NOT NULL, business_day TEXT NOT NULL,
        quota_limit TEXT NOT NULL, available TEXT NOT NULL,
        PRIMARY KEY (station, business_day)
    )''')


def find_budget(db, station_day):
    saved = db.execute('''SELECT quota_limit, available FROM budgets
        WHERE station = ? AND business_day = ?''', station_day.key).fetchone()
    if saved is None:
        return None
    return DayBudget(station_day, decode_int(saved['quota_limit']), decode_int(saved['available']))


def require_budget(db, station_day):
    budget = find_budget(db, station_day)
    if budget is None:
        raise ValueError('站日尚未开账')
    return budget


def open_day(store, station_day, limit):
    store.require_role('hub')
    check_station_day(station_day)
    if type(limit) is not int or limit < 0:
        raise ValueError('初始额度须为非负整数')
    with store.write_transaction() as db:
        saved = find_budget(db, station_day)
        if saved is None:
            amount = encode_int(limit)
            db.execute('INSERT INTO budgets VALUES (?, ?, ?, ?)', (*station_day.key, amount, amount))
        elif saved.limit != limit:
            raise ValueError('本站日已用不同额度开账')
        return DayBudget(station_day, limit, limit)


def get_budget(store, station_day):
    store.require_role('hub')
    check_station_day(station_day)
    with store.read_snapshot() as db:
        return require_budget(db, station_day)
