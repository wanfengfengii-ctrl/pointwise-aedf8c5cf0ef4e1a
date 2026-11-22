"""错峰交班件的导出、解析、前缀重放和联合位置核定。"""
import json
from collections import Counter
from dataclasses import dataclass

from audit.snapshots import WarehouseDaySnapshot
from day.allocations import get_issued_grant
from day.budgets import DayBudget, require_budget
from day.calendar import StationDay, check_name, check_station_day, parse_station_day
from day.handoff import (ACCEPT, BRANCH_EVENT_KINDS, GRANT, HUB_EVENT_KINDS, OPEN, RELEASE,
                         RETURN, SETTLE, handoff_enabled, list_handoff_events)
from day.store import decode_int, encode_int
from exchange.documents import dump_grant, dump_return, load_grant, load_return
from exchange.grants import AcceptedGrant, find_accepted_grant
from exchange.returns import ReturnResult, find_return_result
from exchange.settlements import SettlementResult, find_settlement
from intake.rows import decode_rows, encode_rows
from publish.records import ReleaseRecord, find_release
from publish.usage import QuotaReference

FORMAT = 'shift-handoff/1'


@dataclass(frozen=True, slots=True)
class HandoffEvent:
    kind: str
    value: object


@dataclass(frozen=True, slots=True)
class HandoffPacket:
    role: str
    warehouse_id: str
    peer_id: str
    station_day: StationDay
    events: tuple[HandoffEvent, ...]

    @property
    def position(self) -> int:
        return len(self.events)


def _dump_release(record):
    quota = record.quota
    return {
        'release_id': record.release_id, 'batch_id': record.batch_id,
        'quota': [*quota.station_day.key, quota.warehouse_id, quota.hub_id, quota.grant_id],
        'charged_rows': encode_int(record.charged_rows), 'rows': encode_rows(record.rows),
    }


def _load_release(data):
    station, iso_day, warehouse_id, hub_id, grant_id = data['quota']
    quota = QuotaReference(parse_station_day(station, iso_day), warehouse_id, hub_id, grant_id)
    return ReleaseRecord(data['release_id'], data['batch_id'], quota,
                         decode_int(data['charged_rows']), decode_rows(data['rows']))


def _build_event(db, station_day, kind, ref):
    if kind == OPEN:
        return {'kind': kind, 'limit': encode_int(require_budget(db, station_day).limit)}
    if kind == GRANT:
        document = get_issued_grant(db, ref)
        if document is None:
            raise RuntimeError('交班事件缺少已签发划拨')
        return {'kind': kind, 'document': dump_grant(document)}
    if kind == ACCEPT:
        accepted = find_accepted_grant(db, ref)
        if accepted is None:
            raise RuntimeError('交班事件缺少已接纳划拨')
        return {'kind': kind, 'document': dump_grant(accepted.document)}
    if kind == RELEASE:
        record = find_release(db, ref)
        if record is None:
            raise RuntimeError('交班事件缺少正式成果')
        return {'kind': kind, 'record': _dump_release(record)}
    if kind == RETURN:
        result = find_return_result(db, ref)
        if result is None:
            raise RuntimeError('交班事件缺少正式退额')
        return {'kind': kind, 'document': dump_return(result.document),
                'available_after': encode_int(result.available_after)}
    if kind == SETTLE:
        result = find_settlement(db, ref)
        if result is None:
            raise RuntimeError('交班事件缺少已受理核销')
        return {'kind': kind, 'document': dump_return(result.document),
                'newly_restored': encode_int(result.newly_restored),
                'restored_through': encode_int(result.restored_through),
                'available_after': encode_int(result.available_after)}
    raise RuntimeError('未知交班事件')


def build_handoff_packet(store, db, station_day):
    if not handoff_enabled(db, station_day):
        raise ValueError('本站日未启用交班')
    if store.role == 'hub':
        require_budget(db, station_day)
    events = [_build_event(db, station_day, kind, ref)
              for _, kind, ref in list_handoff_events(db, station_day)]
    packet = {
        'format': FORMAT, 'role': store.role,
        'warehouse_id': store.warehouse_id, 'peer_id': store.peer_id,
        'station': station_day.station, 'business_day': station_day.day.isoformat(),
        'position': len(events), 'events': events,
    }
    return json.dumps(packet, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def _read_text(data, field):
    value = data.get(field)
    if type(value) is not str:
        raise ValueError('交班件内容无效')
    return value


def _parse_event(data):
    if type(data) is not dict:
        raise ValueError('交班件内容无效')
    kind = data.get('kind')
    try:
        if kind == OPEN:
            return HandoffEvent(OPEN, decode_int(_read_text(data, 'limit')))
        if kind in (GRANT, ACCEPT):
            return HandoffEvent(kind, load_grant(_read_text(data, 'document')))
        if kind == RELEASE:
            record = data.get('record')
            if type(record) is not dict:
                raise ValueError('交班件内容无效')
            return HandoffEvent(kind, _load_release(record))
        if kind == RETURN:
            return HandoffEvent(kind, ReturnResult(load_return(_read_text(data, 'document')),
                                                   decode_int(_read_text(data, 'available_after'))))
        if kind == SETTLE:
            return HandoffEvent(kind, SettlementResult(
                load_return(_read_text(data, 'document')),
                decode_int(_read_text(data, 'newly_restored')),
                decode_int(_read_text(data, 'restored_through')),
                decode_int(_read_text(data, 'available_after'))))
    except (KeyError, TypeError, IndexError, ValueError) as error:
        raise ValueError('交班件内容无效') from error
    raise ValueError('交班件含未知事件')


def _check_event_binding(packet, event):
    day, own, peer = packet.station_day, packet.warehouse_id, packet.peer_id
    value = event.value
    if event.kind in (GRANT, ACCEPT):
        document = value
        if document.station_day != day:
            raise ValueError('交班件事件与站日不符')
        if event.kind == GRANT and (document.hub_id, document.branch_id) != (own, peer):
            raise ValueError('交班件事件与仓库归属不符')
        if event.kind == ACCEPT and (document.branch_id, document.hub_id) != (own, peer):
            raise ValueError('交班件事件与仓库归属不符')
    elif event.kind in (RETURN, SETTLE):
        grant = value.document.grant
        if grant.station_day != day:
            raise ValueError('交班件事件与站日不符')
        own_side = (grant.branch_id, grant.hub_id) if event.kind == RETURN else (grant.hub_id, grant.branch_id)
        if own_side != (own, peer):
            raise ValueError('交班件事件与仓库归属不符')
    elif event.kind == RELEASE:
        if value.quota.station_day != day:
            raise ValueError('交班件事件与站日不符')


def parse_handoff_packet(data):
    if isinstance(data, (bytearray, memoryview)):
        data = bytes(data)
    if type(data) is not bytes:
        raise ValueError('交班件须为 bytes')
    try:
        raw = json.loads(data.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError('交班件编码无效') from error
    if type(raw) is not dict or raw.get('format') != FORMAT:
        raise ValueError('交班件格式不符')
    role = raw.get('role')
    if role not in ('hub', 'branch'):
        raise ValueError('交班件仓库角色无效')
    try:
        warehouse_id = check_name(raw.get('warehouse_id'), '仓库')
        peer_id = check_name(raw.get('peer_id'), '对端仓库')
        station_day = parse_station_day(raw.get('station'), raw.get('business_day'))
        position = raw.get('position')
        events = raw.get('events')
        if type(position) is not int or type(events) is not list or position != len(events):
            raise ValueError('交班件截点与事件序不符')
        parsed = tuple(_parse_event(item) for item in events)
    except (KeyError, TypeError) as error:
        raise ValueError('交班件内容无效') from error
    packet = HandoffPacket(role, warehouse_id, peer_id, station_day, parsed)
    allowed = HUB_EVENT_KINDS if role == 'hub' else BRANCH_EVENT_KINDS
    for event in parsed:
        if event.kind not in allowed:
            raise ValueError('交班件事件与仓库角色不符')
        _check_event_binding(packet, event)
    if role == 'hub' and (not parsed or parsed[0].kind != OPEN
                          or any(event.kind == OPEN for event in parsed[1:])):
        raise ValueError('总仓交班件须以开账为首事件')
    return packet


def hub_snapshot_at(packet, position):
    if packet.role != 'hub' or not 0 <= position <= packet.position:
        raise ValueError('总仓交班件前缀位置无效')
    limit = available = None
    issued, settlements, releases = {}, {}, {}
    for index, event in enumerate(packet.events, 1):
        if index > position:
            break
        if event.kind == OPEN:
            limit = available = event.value
        elif event.kind == GRANT:
            issued[event.value.grant_id] = event.value
            available -= event.value.rows
        elif event.kind == RELEASE:
            releases[event.value.release_id] = event.value
            available -= event.value.charged_rows
        elif event.kind == SETTLE:
            settlements[event.value.document.request_id] = event.value
            available += event.value.newly_restored
    budget = None if limit is None else DayBudget(packet.station_day, limit, available)
    return WarehouseDaySnapshot(
        'hub', packet.warehouse_id, packet.peer_id, packet.station_day,
        tuple(releases[key] for key in sorted(releases)), budget=budget,
        issued=tuple(issued[key] for key in sorted(issued)),
        settlements=tuple(settlements[key] for key in sorted(settlements)))


def branch_snapshot_at(packet, position):
    if packet.role != 'branch' or not 0 <= position <= packet.position:
        raise ValueError('分仓交班件前缀位置无效')
    accepted, returns, releases = {}, {}, {}
    for index, event in enumerate(packet.events, 1):
        if index > position:
            break
        if event.kind == ACCEPT:
            accepted[event.value.grant_id] = event.value
        elif event.kind == RETURN:
            returns[event.value.document.request_id] = event.value
        elif event.kind == RELEASE:
            releases[event.value.release_id] = event.value
    consumed = Counter()
    for result in returns.values():
        consumed[result.document.grant.grant_id] += result.document.returned_rows
    for record in releases.values():
        consumed[record.quota.grant_id] += record.charged_rows
    return WarehouseDaySnapshot(
        'branch', packet.warehouse_id, packet.peer_id, packet.station_day,
        tuple(releases[key] for key in sorted(releases)),
        accepted=tuple(AcceptedGrant(document, document.rows - consumed[grant_id])
                       for grant_id, document in sorted(accepted.items())),
        returns=tuple(returns[key] for key in sorted(returns)))


def _prefix_needs(events, kind):
    # 事件位置从 1 起，返回 (位置, 对象) 序列。
    return [(index, event.value) for index, event in enumerate(events, 1) if event.kind == kind]


def select_joint_positions(hub_packet, branch_packet):
    issued = {document.grant_id: (index, document)
              for index, document in _prefix_needs(hub_packet.events, GRANT)}
    returned = {result.document.request_id: (index, result.document)
                for index, result in _prefix_needs(branch_packet.events, RETURN)}
    accepts = _prefix_needs(branch_packet.events, ACCEPT)
    settles = [(index, result.document) for index, result in _prefix_needs(hub_packet.events, SETTLE)]
    hub_bound, branch_bound = hub_packet.position, branch_packet.position
    uncovered = hub_bound + branch_bound + 1
    need_hub = [0] * (branch_bound + 1)
    need_branch = [0] * (hub_bound + 1)
    current = 0
    pending = 0
    for position in range(1, branch_bound + 1):
        while pending < len(accepts) and accepts[pending][0] <= position:
            document = accepts[pending][1]
            hit = issued.get(document.grant_id)
            current = uncovered if hit is None or hit[1] != document else max(current, hit[0])
            pending += 1
        need_hub[position] = current
    current = 0
    pending = 0
    for position in range(1, hub_bound + 1):
        while pending < len(settles) and settles[pending][0] <= position:
            document = settles[pending][1]
            hit = returned.get(document.request_id)
            current = uncovered if hit is None or hit[1] != document else max(current, hit[0])
            pending += 1
        need_branch[position] = current
    # 合格对 (h, b) 满足 need_hub[b] <= h 且 need_branch[h] <= b；
    # 该集合对分量最大值封闭，故分量各自最大的对即支配所有合格对的最晚联合前缀。
    best_hub = best_branch = 0
    for hub_position in range(hub_bound + 1):
        required = need_branch[hub_position]
        if required > branch_bound:
            continue
        for branch_position in range(required, branch_bound + 1):
            if need_hub[branch_position] <= hub_position:
                best_hub = max(best_hub, hub_position)
                best_branch = max(best_branch, branch_position)
    return best_hub, best_branch
