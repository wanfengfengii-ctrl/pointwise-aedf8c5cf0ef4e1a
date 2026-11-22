"""Public inputs for the two shift-close orders, runnable against the original warehouse."""
from audit.lookup import read_batch
from audit.reconcile import audit_day, audit_handoff
from audit.snapshots import read_day_snapshot, read_handoff
from day.allocations import grant
from day.budgets import open_day
from day.calendar import parse_station_day
from day.workspaces import enable_handoff, open_branch_store, open_hub_store
from demo import emit_scenario
from exchange.grants import accept_grant
from exchange.returns import return_quota
from exchange.settlements import settle_return
from intake.batches import stage
from publish.commit import publish_batch

DAY = parse_station_day("石桥站", "2026-11-21")
HUB = "总仓"
BRANCH = "北岸分仓"
KEY = b"local-shift-audit-fixture"


class InterruptedPublish(RuntimeError):
    pass


def interrupt_publish():
    raise InterruptedPublish("发布尚未提交")


def run_trace(early, *, hub_path=":memory:", branch_path=":memory:",
              enable=None, capture=read_day_snapshot):
    packets = {}
    with (open_hub_store(hub_path, HUB, BRANCH, KEY) as hub,
          open_branch_store(branch_path, BRANCH, HUB, KEY) as branch):
        if enable is not None:
            enable(hub, DAY)
            enable(branch, DAY)
        open_day(hub, DAY, 2)
        g1 = grant(hub, DAY, "G1", BRANCH, 2)
        accept_grant(branch, g1)
        r1 = return_quota(branch, DAY, "G1", "R1", 1).document
        packets["branch_r1"] = capture(branch, DAY)
        r2 = return_quota(branch, DAY, "G1", "R2", 1).document
        packets["branch_r2"] = capture(branch, DAY)
        stage(hub, DAY, "空班报", "总仓原表", [])
        if early:
            publish_batch(hub, DAY, "空班报", "Z")
        packets["hub_before_r2"] = capture(hub, DAY)
        s2 = settle_return(hub, r2)
        if settle_return(hub, r2) != s2:
            raise AssertionError("R2 重投改变首执")
        g2 = grant(hub, DAY, "G2", BRANCH, 2)
        if not early:
            publish_batch(hub, DAY, "空班报", "Z")
        packets["hub_g2"] = capture(hub, DAY)
        accept_grant(branch, g2)
        stage(branch, DAY, "接班补报", "北岸补报原表", [(17, 7000, "ml")])
        try:
            publish_batch(branch, DAY, "接班补报", "B", "G2", before_commit=interrupt_publish)
        except InterruptedPublish:
            packets["branch_failed"] = capture(branch, DAY)
        else:
            raise AssertionError("未触发预定的发布中断")
        original = publish_batch(branch, DAY, "接班补报", "B", "G2")
        if publish_batch(branch, DAY, "接班补报", "B", "G2") != original:
            raise AssertionError("B 重投改变原成果或回执")
        packets["branch_done"] = capture(branch, DAY)
        s1 = settle_return(hub, r1)
        packets["hub_done"] = capture(hub, DAY)
        views = {"Z": read_batch(hub, "Z"), "B": read_batch(branch, "B")}
    return packets, views, (s2.newly_restored, s1.newly_restored)


def run_handoff(early):
    # 两仓各自启用交班后重跑同一剧本，取件改为固定交班件。
    return run_trace(early, enable=enable_handoff, capture=read_handoff)[0]


def _line_view(line):
    if line is None:
        return None
    return {"published": line.published, "branch_available": line.branch_available,
            "returned": line.returned, "restored": line.restored,
            "pending_acceptance": line.pending_acceptance, "pending_return": line.pending_return}


def audit_row(hub_packet, branch_packet):
    result = audit_handoff(DAY, hub_packet, branch_packet)
    report = result["audit"]
    lines = {line.grant.grant_id: _line_view(line) for line in report.quota.grants}
    return {
        "positions": [result["hub_position"], result["branch_position"]],
        "hub_releases": [record.release_id for record in result["hub"].releases],
        "branch_releases": [record.release_id for record in result["branch"].releases],
        "grants": {grant_id: lines.get(grant_id) for grant_id in ("G1", "G2")},
        "joint": [report.joint.rows, report.joint.missing, report.joint.litres],
        "pending_acceptance": report.quota.pending_acceptance,
        "pending_return": report.quota.pending_return,
        "balanced": report.quota.balanced,
        "branch_rows": [[row.litres, row.origin.source, row.origin.source_line, row.origin.position]
                        for record in result["branch"].releases for row in record.rows],
    }


def handoff_rows(early_packets, late_packets):
    # 剧本取件组合：前两行反向错峰，末行两序同账。
    return [
        ("hub_g2+branch_r1/Z早", early_packets["hub_g2"], early_packets["branch_r1"]),
        ("hub_g2+branch_r1/Z晚", late_packets["hub_g2"], late_packets["branch_r1"]),
        ("hub_before_r2+branch_done/Z早", early_packets["hub_before_r2"], early_packets["branch_done"]),
        ("hub_before_r2+branch_done/Z晚", late_packets["hub_before_r2"], late_packets["branch_done"]),
        ("hub_g2+branch_r2", early_packets["hub_g2"], early_packets["branch_r2"]),
        ("hub_g2+branch_failed", early_packets["hub_g2"], early_packets["branch_failed"]),
        ("hub_done+branch_done/Z早", early_packets["hub_done"], early_packets["branch_done"]),
        ("hub_done+branch_done/Z晚", late_packets["hub_done"], late_packets["branch_done"]),
    ]


def _g1(published, available, returned, restored, pending_acceptance, pending_return):
    return {"published": published, "branch_available": available, "returned": returned,
            "restored": restored, "pending_acceptance": pending_acceptance,
            "pending_return": pending_return}


def expected_handoff_rows():
    g1_partial = _g1(0, 1, 1, 0, 0, 1)
    g1_in_transit = _g1(0, 0, 2, 0, 0, 2)
    g1_restored = _g1(0, 0, 2, 2, 0, 0)
    return [
        {"name": "hub_g2+branch_r1/Z早", "positions": [3, 2], "hub_releases": ["Z"],
         "branch_releases": [], "grants": {"G1": g1_partial, "G2": None}, "joint": [0, 0, 0],
         "pending_acceptance": 0, "pending_return": 1, "balanced": True, "branch_rows": []},
        {"name": "hub_g2+branch_r1/Z晚", "positions": [2, 2], "hub_releases": [],
         "branch_releases": [], "grants": {"G1": g1_partial, "G2": None}, "joint": [0, 0, 0],
         "pending_acceptance": 0, "pending_return": 1, "balanced": True, "branch_rows": []},
        {"name": "hub_before_r2+branch_done/Z早", "positions": [3, 3], "hub_releases": ["Z"],
         "branch_releases": [], "grants": {"G1": g1_in_transit, "G2": None}, "joint": [0, 0, 0],
         "pending_acceptance": 0, "pending_return": 2, "balanced": True, "branch_rows": []},
        {"name": "hub_before_r2+branch_done/Z晚", "positions": [2, 3], "hub_releases": [],
         "branch_releases": [], "grants": {"G1": g1_in_transit, "G2": None}, "joint": [0, 0, 0],
         "pending_acceptance": 0, "pending_return": 2, "balanced": True, "branch_rows": []},
        {"name": "hub_g2+branch_r2", "positions": [5, 3], "hub_releases": ["Z"],
         "branch_releases": [], "grants": {"G1": g1_restored, "G2": _g1(0, 0, 0, 0, 2, 0)},
         "joint": [0, 0, 0], "pending_acceptance": 2, "pending_return": 0,
         "balanced": True, "branch_rows": []},
        {"name": "hub_g2+branch_failed", "positions": [5, 4], "hub_releases": ["Z"],
         "branch_releases": [], "grants": {"G1": g1_restored, "G2": _g1(0, 2, 0, 0, 0, 0)},
         "joint": [0, 0, 0], "pending_acceptance": 0, "pending_return": 0,
         "balanced": True, "branch_rows": []},
        {"name": "hub_done+branch_done/Z早", "positions": [6, 5], "hub_releases": ["Z"],
         "branch_releases": ["B"], "grants": {"G1": g1_restored, "G2": _g1(1, 1, 0, 0, 0, 0)},
         "joint": [1, 0, 7], "pending_acceptance": 0, "pending_return": 0,
         "balanced": True, "branch_rows": [[7, "北岸补报原表", 17, 1]]},
        {"name": "hub_done+branch_done/Z晚", "positions": [6, 5], "hub_releases": ["Z"],
         "branch_releases": ["B"], "grants": {"G1": g1_restored, "G2": _g1(1, 1, 0, 0, 0, 0)},
         "joint": [1, 0, 7], "pending_acceptance": 0, "pending_return": 0,
         "balanced": True, "branch_rows": [[7, "北岸补报原表", 17, 1]]},
    ]


def collect_handoff_rows(early_packets, late_packets):
    return [dict(name=name, **audit_row(hub_packet, branch_packet))
            for name, hub_packet, branch_packet in handoff_rows(early_packets, late_packets)]


def main():
    early, early_views, amounts = run_trace(True)
    late, late_views, _ = run_trace(False)
    inputs = {"limit": 2, "grants": [2, 2], "returns": [1, 1], "receive": ["R2", "R2", "R1"]}
    expected = {"same_caps": True, "same_z": True, "difference": [-1, -1], "opposite_difference": -2}
    actual = {
        "same_caps": (early["hub_g2"], early["branch_r1"]) == (late["hub_g2"], late["branch_r1"]),
        "same_z": early_views["Z"] == late_views["Z"],
        "difference": [audit_day(DAY, trace["hub_g2"], trace["branch_r1"]).quota.difference
                       for trace in (early, late)],
        "opposite_difference": audit_day(DAY, early["hub_before_r2"], early["branch_done"]).quota.difference,
    }
    full = audit_day(DAY, early["hub_done"], early["branch_done"])
    row = early_views["B"].rows[0]
    downstream_expected = {"restored": [2, 0], "balanced": True, "litres": 7, "origin": ["北岸补报原表", 17, 1]}
    downstream_actual = {"restored": list(amounts), "balanced": full.quota.balanced,
                         "litres": full.joint.litres, "origin": [row.origin.source, row.origin.source_line,
                                                                 row.origin.position]}
    emit_scenario("shift-caps-v1", inputs, expected, actual, downstream_expected, downstream_actual,
                  ["publish/commit.py", "exchange/settlements.py", "audit/snapshots.py", "audit/reconcile.py"],
                  "总仓用后件恢复两行，两组终态相同，分仓早账只退一行。反向错峰时，G2 在总仓早账尚未签发。")
    early_packets = run_handoff(True)
    late_packets = run_handoff(False)
    handoff_actual = collect_handoff_rows(early_packets, late_packets)
    emit_scenario("shift-handoff-v2", dict(inputs, z_order=["early", "late"]),
                  expected_handoff_rows(), handoff_actual,
                  {"offline": True, "source": "packets-only"}, {"offline": True, "source": "packets-only"},
                  ["day/handoff.py", "audit/handoff.py", "audit/snapshots.py", "audit/reconcile.py"],
                  "交班件各自固定截点，离线核定最晚联合前缀。Z 早发保留、晚发排除；"
                  "退额核销前在途，后件可先核销；G2 未签发前接纳不入账。")


if __name__ == "__main__":
    main()
