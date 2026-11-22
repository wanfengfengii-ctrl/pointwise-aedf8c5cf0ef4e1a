import json
from dataclasses import astuple
from pathlib import Path
from tempfile import TemporaryDirectory

from audit.lookup import read_batch
from audit.reconcile import audit_day, audit_handoff
from audit.snapshots import read_day_snapshot, read_handoff
from day.allocations import grant
from day.balance import QuotaExceeded
from day.budgets import get_budget, open_day
from day.calendar import parse_station_day
from day.workspaces import enable_handoff, open_branch_store, open_hub_store
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


def interrupt_commit():
    raise RuntimeError('提交前中断')


def run_shift_handoff(directory):
    """错峰交班：两仓关闭后，仅凭交班件离线核定联合前缀、成果和在途量。"""
    shift_day = parse_station_day('石桥站', '2026-11-22')
    hub_path = directory / 'shift-hub.sqlite'
    branch_path = directory / 'shift-north.sqlite'
    with open_hub_store(hub_path, hub_id, branch_id, demo_key) as hub, \
            open_branch_store(branch_path, branch_id, hub_id, demo_key) as branch:
        enable_handoff(hub, shift_day)
        enable_handoff(branch, shift_day)
        open_day(hub, shift_day, 2)
        g1 = grant(hub, shift_day, 'G1', branch_id, 2)
        accept_grant(branch, g1)
        r1 = return_quota(branch, shift_day, 'G1', 'R1', 1).document
        branch_r1 = read_handoff(branch, shift_day)
        r2 = return_quota(branch, shift_day, 'G1', 'R2', 1).document
        stage(hub, shift_day, '空班报', '总仓原表', [])
        publish_batch(hub, shift_day, '空班报', 'Z')
        settle_return(hub, r2)
        settle_return(hub, r2)
        g2 = grant(hub, shift_day, 'G2', branch_id, 2)
        hub_g2 = read_handoff(hub, shift_day)
        accept_grant(branch, g2)
        stage(branch, shift_day, '补报', '北岸补报原表', [(17, 7000, 'ml')])
        try:
            publish_batch(branch, shift_day, '补报', 'B', 'G2', before_commit=interrupt_commit)
        except RuntimeError:
            pass
        publish_batch(branch, shift_day, '补报', 'B', 'G2')
        branch_done = read_handoff(branch, shift_day)
        settle_return(hub, r1)
        hub_done = read_handoff(hub, shift_day)
    # 两仓均已关闭，以下只凭交班件 bytes 离线核账。
    staggered = audit_handoff(shift_day, hub_g2, branch_r1)
    final = audit_handoff(shift_day, hub_done, branch_done)
    staggered_g1 = staggered['audit'].quota.grants[0]
    actual = {'positions': [staggered['hub_position'], staggered['branch_position']],
              'hub_releases': [record.release_id for record in staggered['hub'].releases],
              'g2_issued': any(line.grant.grant_id == 'G2' for line in staggered['audit'].quota.grants),
              'g1_branch_available': staggered_g1.branch_available,
              'g1_pending_return': staggered_g1.pending_return,
              'balanced': staggered['audit'].quota.balanced}
    final_b = final['branch'].releases[0].rows[0]
    downstream_actual = {'positions': [final['hub_position'], final['branch_position']],
                         'releases': [record.release_id for record in final['hub'].releases]
                         + [record.release_id for record in final['branch'].releases],
                         'joint': list(astuple(final['audit'].joint)),
                         'in_transit': final['audit'].quota.pending_acceptance
                         + final['audit'].quota.pending_return,
                         'b_row': [final_b.litres, final_b.origin.source,
                                   final_b.origin.source_line, final_b.origin.position],
                         'balanced': final['audit'].quota.balanced}
    emit_scenario('shift-offline', {'limit': 2, 'grants': [2, 2], 'returns': [1, 1],
                                    'receive': ['R2', 'R2', 'R1'], 'z': 'early',
                                    'packets': ['hub_g2', 'branch_r1', 'hub_done', 'branch_done']},
                  {'positions': [3, 2], 'hub_releases': ['Z'], 'g2_issued': False,
                   'g1_branch_available': 1, 'g1_pending_return': 1, 'balanced': True}, actual,
                  {'positions': [6, 5], 'releases': ['Z', 'B'], 'joint': [1, 0, 7],
                   'in_transit': 0, 'b_row': [7, '北岸补报原表', 17, 1], 'balanced': True},
                  downstream_actual,
                  ['day/handoff.py', 'audit/handoff.py', 'audit/snapshots.py', 'audit/reconcile.py'],
                  '源仓关闭后凭交班件断源核账：分仓早件只到 R1 时联合前缀退到 (3,2)，'
                  'Z 保留、G2 未签发、退额 1 行在途；终件核定 (6,5)，B 的 7 升落定，在途清零。')


if __name__ == '__main__':
    with TemporaryDirectory() as directory:
        run(Path(directory))
        run_shift_handoff(Path(directory))
