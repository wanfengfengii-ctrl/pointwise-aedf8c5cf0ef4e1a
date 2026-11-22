import json
from dataclasses import astuple
from pathlib import Path
from tempfile import TemporaryDirectory

from audit.lookup import read_batch
from audit.reconcile import audit_day
from audit.snapshots import read_day_snapshot
from day.allocations import grant
from day.balance import QuotaExceeded
from day.budgets import get_budget, open_day
from day.calendar import parse_station_day
from day.workspaces import open_branch_store, open_hub_store
from exchange.grants import accept_grant
from exchange.returns import return_quota
from exchange.settlements import settle_return
from intake.batches import stage
from publish.commit import publish_batch


station_day = parse_station_day('石桥站', '2026-11-21')
hub_id, branch_id = '总仓', '北岸分仓'
demo_key = b'local-watershed-demo-key'
branch_entries = [(12, 7000, 'ml'), (16, None, 'L'), (20, 11, 'L')]
hub_values = [2, 0, None, 3, 5, 5, 8]


def emit_scenario(case_id, inputs, expected, actual, downstream_expected, downstream_actual, paths, oracle):
    if actual != expected or downstream_actual != downstream_expected:
        raise AssertionError(f'{case_id} 业务核对失败：{actual!r}, {downstream_actual!r}')
    evidence = dict(case_id=case_id, input=inputs, expected=expected, actual=actual,
                    downstream_expected=downstream_expected, downstream_actual=downstream_actual,
                    code_paths=paths, oracle=oracle)
    print('POINTWISE_SCENARIO ' + json.dumps(evidence, ensure_ascii=False, separators=(',', ':')))


def run(directory):
    hub_path = directory / 'hub.sqlite'
    branch_path = directory / 'north.sqlite'
    with open_hub_store(hub_path, hub_id, branch_id, demo_key) as hub, \
            open_branch_store(branch_path, branch_id, hub_id, demo_key) as branch:
        open_day(hub, station_day, 10)
        document = grant(hub, station_day, '北岸-划拨', branch_id, 6)
        accept_grant(branch, document)
        stage(branch, station_day, '北岸-晨报', '北岸晨班原表', branch_entries)
        _, branch_receipt = publish_batch(branch, station_day, '北岸-晨报', '北岸-上午', document.grant_id)
        initial_view = read_batch(branch, '北岸-上午')
        early = return_quota(branch, station_day, document.grant_id, '北岸-退一', 1)
        later = return_quota(branch, station_day, document.grant_id, '北岸-退二', 2)
        first_result = settle_return(hub, later.document)
        replay = settle_return(hub, later.document)
        last_result = settle_return(hub, early.document)
        available = get_budget(hub, station_day).available
        entries = [(number, value, 'L') for number, value in enumerate(hub_values, 1)]
        stage(hub, station_day, '总仓-日报', '总仓现场表', entries)
        _, hub_receipt = publish_batch(hub, station_day, '总仓-日报', '总仓-正式')
        stage(hub, station_day, '总仓-补报', '总仓补报表', [(8, 9, 'L')])
        rejected = False
        try:
            publish_batch(hub, station_day, '总仓-补报', '总仓-补发')
        except QuotaExceeded:
            rejected = True

    with open_hub_store(hub_path, hub_id, branch_id, demo_key) as hub, \
            open_branch_store(branch_path, branch_id, hub_id, demo_key) as branch:
        view = read_batch(branch, '北岸-上午')
        hub_view = read_batch(hub, '总仓-正式')
        report = audit_day(station_day, read_day_snapshot(hub, station_day), read_day_snapshot(branch, station_day))
        restored = {'first_arrival': first_result.newly_restored, 'earlier_arrival': last_result.newly_restored,
                    'same_replay': first_result == replay, 'hub_available': available,
                    'branch_available': report.quota.branch_available}
        emit_scenario('n5', {'limit': 10, 'grant': 6, 'branch_rows': branch_entries,
                             'returns': [1, 2], 'delivery': [2, 2, 1]},
                      {'first_arrival': 3, 'earlier_arrival': 0, 'same_replay': True,
                       'hub_available': 7, 'branch_available': 0}, restored,
                      {'hub_rows': 7, 'branch_rows': 3, 'eighth_rejected': True, 'balanced': True},
                      {'hub_rows': report.hub.rows, 'branch_rows': report.branch.rows,
                       'eighth_rejected': rejected, 'balanced': report.quota.balanced},
                      ['exchange/returns.py', 'exchange/settlements.py', 'publish/commit.py', 'audit/reconcile.py'],
                      '初始自留 10 减 6 为 4。分仓发布 3 并退 1 加 2，累计件先到只恢复 3，所以总仓可发布 7。')
        emit_scenario('n8', {'branch_rows': branch_entries, 'hub_litres': hub_values},
                      {'branch_litres': [7, None, 11], 'branch_sources': [['北岸晨班原表', 12], ['北岸晨班原表', 16],
                         ['北岸晨班原表', 20]], 'hub_litres': hub_values, 'history_equal': True},
                      {'branch_litres': [row.litres for row in view.rows],
                       'branch_sources': [[row.origin.source, row.origin.source_line] for row in view.rows],
                       'hub_litres': [row.litres for row in hub_view.rows],
                       'history_equal': view == initial_view and view.receipt == branch_receipt and hub_view.receipt == hub_receipt},
                      {'hub': (7, 1, 23), 'branch': (3, 1, 18), 'joint': (10, 2, 41), 'difference': 0},
                      {'hub': astuple(report.hub), 'branch': astuple(report.branch),
                       'joint': astuple(report.joint), 'difference': report.quota.difference},
                      ['intake/units.py', 'intake/rows.py', 'audit/lookup.py', 'audit/history.py',
                       'audit/snapshots.py', 'audit/totals.py', 'audit/reconcile.py'],
                      '逐项换算得到 7、缺测、11。对照源行号 12、16、20。逐仓手加 23 与 18 升，保留两条缺测行。')


if __name__ == '__main__':
    with TemporaryDirectory() as directory:
        run(Path(directory))
