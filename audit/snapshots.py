from dataclasses import dataclass

from day.allocations import list_issued_grants
from day.budgets import DayBudget, require_budget
from day.calendar import StationDay, check_station_day
from exchange.documents import GrantDocument
from exchange.grants import AcceptedGrant, list_accepted_grants
from exchange.returns import ReturnResult, list_day_returns
from exchange.settlements import SettlementResult, list_day_settlements
from publish.records import ReleaseRecord, list_day_releases


@dataclass(frozen=True, slots=True)
class WarehouseDaySnapshot:
    role: str
    warehouse_id: str
    peer_id: str
    station_day: StationDay
    releases: tuple[ReleaseRecord, ...]
    budget: DayBudget | None = None
    issued: tuple[GrantDocument, ...] = ()
    accepted: tuple[AcceptedGrant, ...] = ()
    returns: tuple[ReturnResult, ...] = ()
    settlements: tuple[SettlementResult, ...] = ()


def read_day_snapshot(store, station_day):
    check_station_day(station_day)
    with store.read_snapshot() as db:
        releases = list_day_releases(db, station_day)
        identity = (store.role, store.warehouse_id, store.peer_id, station_day, releases)
        if store.role == 'hub':
            return WarehouseDaySnapshot(*identity, budget=require_budget(db, station_day),
                                        issued=list_issued_grants(db, station_day),
                                        settlements=list_day_settlements(db, station_day))
        return WarehouseDaySnapshot(*identity, accepted=list_accepted_grants(db, station_day),
                                    returns=list_day_returns(db, station_day))
