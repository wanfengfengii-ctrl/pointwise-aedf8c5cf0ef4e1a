from day.budgets import require_budget
from day.store import encode_int


class QuotaExceeded(ValueError):
    """当前发布资格无法覆盖本次请求。"""


def require_positive(value):
    if type(value) is not int or value <= 0:
        raise ValueError('行额度须为正整数')
    return value


def require_available(available, count):
    if type(count) is not int or count < 0:
        raise ValueError('占额须为非负整数')
    if count > available:
        raise QuotaExceeded('当前行额度不足')
    return available - count


def hub_available(db, station_day):
    return require_budget(db, station_day).available


def _save_available(db, station_day, amount):
    db.execute('''UPDATE budgets SET available = ?
        WHERE station = ? AND business_day = ?''', (encode_int(amount), *station_day.key))
    return amount


def debit_hub(db, station_day, count):
    remaining = require_available(hub_available(db, station_day), count)
    return _save_available(db, station_day, remaining)


def credit_hub(db, station_day, count):
    if type(count) is not int or count < 0:
        raise ValueError('恢复额度须为非负整数')
    budget = require_budget(db, station_day)
    available = budget.available + count
    if available > budget.limit:
        raise ValueError('恢复后超过站日初始额度')
    return _save_available(db, station_day, available)
