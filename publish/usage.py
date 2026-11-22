from dataclasses import dataclass

from day.balance import debit_hub, hub_available
from day.calendar import StationDay, check_name, check_station_day
from exchange.grants import consume_grant_rows, require_accepted_grant


@dataclass(frozen=True, slots=True)
class QuotaReference:
    station_day: StationDay
    warehouse_id: str
    hub_id: str
    grant_id: str | None


def make_quota_reference(store, db, station_day, grant_id=None):
    check_station_day(station_day)
    if store.role == 'hub':
        if grant_id is not None:
            raise ValueError('总仓发布使用站日资格')
        hub_available(db, station_day)
        return QuotaReference(station_day, store.warehouse_id, store.warehouse_id, None)
    check_name(grant_id, '分仓发布的划拨号')
    accepted = require_accepted_grant(db, station_day, grant_id)
    return QuotaReference(station_day, accepted.document.branch_id, accepted.document.hub_id, grant_id)


def charge_release_rows(db, reference, count):
    if reference.grant_id is None:
        return debit_hub(db, reference.station_day, count)
    return consume_grant_rows(db, reference.station_day, reference.grant_id, count)
