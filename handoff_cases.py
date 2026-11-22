"""Public inputs for the two shift-close orders, runnable against the original warehouse."""
from audit.lookup import read_batch
from audit.reconcile import audit_day
from audit.snapshots import read_day_snapshot
from day.allocations import grant
from day.budgets import open_day
from day.calendar import parse_station_day
from day.workspaces import open_branch_store, open_hub_store
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


if __name__ == "__main__":
    main()
