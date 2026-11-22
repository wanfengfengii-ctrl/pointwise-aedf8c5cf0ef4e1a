from collections import Counter
from dataclasses import dataclass

from audit.handoff import (branch_snapshot_at, hub_snapshot_at, parse_handoff_packet,
                           select_joint_positions)
from audit.snapshots import WarehouseDaySnapshot
from audit.totals import SiteTotals, sum_release_metrics
from day.calendar import StationDay, check_station_day
from exchange.documents import GrantDocument
from publish.usage import QuotaReference


@dataclass(frozen=True, slots=True)
class GrantReconciliation:
    grant: GrantDocument
    published: int
    branch_available: int
    returned: int
    restored: int
    pending_acceptance: int
    pending_return: int


@dataclass(frozen=True, slots=True)
class QuotaReconciliation:
    daily_limit: int
    published: int
    hub_available: int
    branch_available: int
    pending_acceptance: int
    pending_return: int
    difference: int
    grants: tuple[GrantReconciliation, ...]
    issues: tuple[str, ...]

    @property
    def balanced(self):
        return self.difference == 0 and not self.issues


@dataclass(frozen=True, slots=True)
class AuditDayResult:
    station_day: StationDay
    hub: SiteTotals
    branch: SiteTotals
    joint: SiteTotals
    quota: QuotaReconciliation


def _check_snapshots(station_day, hub, branch):
    check_station_day(station_day)
    if type(hub) is not WarehouseDaySnapshot or type(branch) is not WarehouseDaySnapshot:
        raise ValueError('须提供两份当地日账快照')
    if hub.station_day != station_day or branch.station_day != station_day:
        raise ValueError('两份日账须与指定站日一致')
    if (hub.role, branch.role) != ('hub', 'branch') or hub.budget is None:
        raise ValueError('须提供总仓日账和分仓日账')
    if (hub.warehouse_id, hub.peer_id) != (branch.peer_id, branch.warehouse_id):
        raise ValueError('两份日账的仓库归属不匹配')


def _grant_quota_lines(hub, branch):
    issues = []
    issued = {grant.grant_id: grant for grant in hub.issued}
    accepted = {item.document: item.available for item in branch.accepted}
    returned = Counter()
    restored = Counter()
    published = Counter()
    for result in branch.returns:
        returned[result.document.grant] += result.document.returned_rows
    for result in hub.settlements:
        document = result.document.grant
        restored[document] = max(restored[document], result.restored_through)
    for record in branch.releases:
        published[record.quota] += len(record.rows)

    for item in branch.accepted:
        document = item.document
        if issued.get(document.grant_id) != document:
            issues.append(f'{document.grant_id} 的分仓接纳未匹配总仓签发')

    lines = []
    for document in hub.issued:
        grant_id = document.grant_id
        reference = QuotaReference(document.station_day, document.branch_id, document.hub_id, grant_id)
        branch_count = published[reference]
        unused = accepted.get(document, 0)
        sent = returned[document] if document in accepted else 0
        credited = restored[document]
        pending_acceptance = 0 if document in accepted else document.rows
        if document in accepted and branch_count + unused + sent != document.rows:
            issues.append(f'{grant_id} 的分仓发布、可用及已退合计与划拨不符')
        if credited > sent:
            issues.append(f'{grant_id} 的总仓已恢复量超过分仓快照已退量')
        lines.append(GrantReconciliation(document, branch_count if document in accepted else 0,
                                         unused, sent, credited, pending_acceptance, max(0, sent - credited)))
    return tuple(lines), tuple(issues)


def reconcile_quota(station_day, hub, branch):
    _check_snapshots(station_day, hub, branch)
    grant_lines, issues = _grant_quota_lines(hub, branch)
    published = sum(len(record.rows) for record in (*hub.releases, *branch.releases))
    branch_available = sum(grant.available for grant in branch.accepted)
    pending_acceptance = sum(line.pending_acceptance for line in grant_lines)
    pending_return = sum(line.pending_return for line in grant_lines)
    accounted = published + hub.budget.available + branch_available + pending_acceptance + pending_return
    return QuotaReconciliation(hub.budget.limit, published, hub.budget.available, branch_available,
                               pending_acceptance, pending_return, hub.budget.limit - accounted,
                               grant_lines, issues)


def audit_day(station_day, hub, branch):
    quota = reconcile_quota(station_day, hub, branch)
    hub_totals = sum_release_metrics(hub.releases)
    branch_totals = sum_release_metrics(branch.releases)
    joint = SiteTotals(hub_totals.rows + branch_totals.rows, hub_totals.missing + branch_totals.missing,
                       hub_totals.litres + branch_totals.litres)
    return AuditDayResult(station_day, hub_totals, branch_totals, joint, quota)


def audit_handoff(station_day, hub_packet, branch_packet):
    check_station_day(station_day)
    hub = parse_handoff_packet(hub_packet)
    branch = parse_handoff_packet(branch_packet)
    if (hub.role, branch.role) != ('hub', 'branch'):
        raise ValueError('须提供总仓交班件和分仓交班件')
    if hub.station_day != station_day or branch.station_day != station_day:
        raise ValueError('两份交班件须与指定站日一致')
    if (hub.warehouse_id, hub.peer_id) != (branch.peer_id, branch.warehouse_id):
        raise ValueError('两份交班件的仓库归属不匹配')
    hub_position, branch_position = select_joint_positions(hub, branch)
    hub_snapshot = hub_snapshot_at(hub, hub_position)
    branch_snapshot = branch_snapshot_at(branch, branch_position)
    return {
        'hub_position': hub_position,
        'branch_position': branch_position,
        'hub': hub_snapshot,
        'branch': branch_snapshot,
        'audit': audit_day(station_day, hub_snapshot, branch_snapshot),
    }
