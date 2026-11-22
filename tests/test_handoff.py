import json
from unittest import TestCase

from audit.handoff import branch_snapshot_at, hub_snapshot_at, parse_handoff_packet
from audit.reconcile import audit_day, audit_handoff
from audit.snapshots import read_day_snapshot, read_handoff
from day.allocations import grant
from day.budgets import get_budget, open_day
from day.workspaces import enable_handoff, open_branch_store, open_hub_store
from exchange.grants import accept_grant
from exchange.returns import return_quota
from exchange.settlements import settle_return
from handoff_cases import collect_handoff_rows, expected_handoff_rows, run_handoff
from intake.batches import stage
from publish.commit import publish_batch
from publish.prepare import prepare_batch
from tests import BRANCH, BRANCH_ROWS, DAY, HUB, KEY, NEXT_DAY, WarehouseCase, fail_before_commit


def packet_position(store, day=DAY):
    return json.loads(read_handoff(store, day))['position']


def packet_kinds(store, day=DAY):
    return [event['kind'] for event in json.loads(read_handoff(store, day))['events']]


class HandoffEnableTests(WarehouseCase):
    def test_enable_repeat_and_staging_is_not_formal(self):
        stage(self.branch, DAY, 'draft', '北岸晨报', BRANCH_ROWS)
        self.assertIsNone(enable_handoff(self.branch, DAY))
        self.assertIsNone(enable_handoff(self.branch, DAY))
        self.assertIsNone(enable_handoff(self.hub, DAY))
        open_day(self.hub, DAY, 2)
        self.assertEqual(packet_position(self.hub), 1)

    def test_enable_after_formal_change_rejected(self):
        open_day(self.hub, DAY, 2)
        with self.assertRaisesRegex(ValueError, '正式变更'):
            enable_handoff(self.hub, DAY)
        document = grant(self.hub, DAY, 'G', BRANCH, 2)
        accept_grant(self.branch, document)
        with self.assertRaisesRegex(ValueError, '正式变更'):
            enable_handoff(self.branch, DAY)
        self.assertEqual(get_budget(self.hub, DAY).available, 0)
        self.assertEqual(self.branch_available(), 2)

    def test_legacy_day_keeps_original_audit(self):
        self.allocate()
        self.load(self.branch, 'observed', [7, None, 11], 'G')
        with self.assertRaisesRegex(ValueError, '未启用交班'):
            read_handoff(self.hub, DAY)
        with self.assertRaisesRegex(ValueError, '未启用交班'):
            read_handoff(self.branch, DAY)
        report = audit_day(DAY, *self.snapshots())
        self.assertTrue(report.quota.balanced)
        self.assertEqual(report.branch.rows, 3)

    def test_hub_export_requires_open_day_and_branch_empty_prefix(self):
        enable_handoff(self.hub, DAY)
        enable_handoff(self.branch, DAY)
        with self.assertRaisesRegex(ValueError, '尚未开账'):
            read_handoff(self.hub, DAY)
        branch_packet = read_handoff(self.branch, DAY)
        self.assertIsInstance(branch_packet, bytes)
        open_day(self.hub, DAY, 0)
        result = audit_handoff(DAY, read_handoff(self.hub, DAY), branch_packet)
        self.assertEqual((result['hub_position'], result['branch_position']), (1, 0))
        self.assertEqual(result['audit'].joint.rows, 0)
        self.assertTrue(result['audit'].quota.balanced)


class HandoffPositionTests(WarehouseCase):
    def setUp(self):
        super().setUp()
        enable_handoff(self.hub, DAY)
        enable_handoff(self.branch, DAY)

    def test_only_first_successful_commit_advances(self):
        open_day(self.hub, DAY, 2)
        document = grant(self.hub, DAY, 'G1', BRANCH, 2)
        accept_grant(self.branch, document)
        stage(self.branch, DAY, 'b', '北岸补报原表', [(17, 7000, 'ml')])
        prepare_batch(self.branch, DAY, 'b', 'B', 'G1')
        self.assertEqual((packet_position(self.hub), packet_position(self.branch)), (2, 1))
        first = return_quota(self.branch, DAY, 'G1', 'R1', 1)
        self.assertEqual(return_quota(self.branch, DAY, 'G1', 'R1', 1), first)
        with self.assertRaisesRegex(RuntimeError, '提交前中断'):
            return_quota(self.branch, DAY, 'G1', 'RX', 1, before_commit=fail_before_commit)
        second = return_quota(self.branch, DAY, 'G1', 'R2', 1)
        settled = settle_return(self.hub, second.document)
        self.assertEqual(settle_return(self.hub, second.document), settled)
        zero_added = settle_return(self.hub, first.document)
        self.assertEqual(zero_added.newly_restored, 0)
        stage(self.hub, DAY, 'empty', '总仓原表', [])
        publish_batch(self.hub, DAY, 'empty', 'Z')
        g2 = grant(self.hub, DAY, 'G2', BRANCH, 2)
        accept_grant(self.branch, g2)
        with self.assertRaisesRegex(RuntimeError, '提交前中断'):
            publish_batch(self.branch, DAY, 'b', 'B', 'G2', before_commit=fail_before_commit)
        release = publish_batch(self.branch, DAY, 'b', 'B', 'G2')
        self.assertEqual(publish_batch(self.branch, DAY, 'b', 'B', 'G2'), release)
        self.assertEqual(packet_kinds(self.hub),
                         ['open', 'grant', 'settle', 'settle', 'release', 'grant'])
        self.assertEqual(packet_kinds(self.branch),
                         ['accept', 'return', 'return', 'accept', 'release'])
        self.assertEqual((packet_position(self.hub), packet_position(self.branch)), (6, 5))

    def test_export_does_not_advance_and_packets_are_fixed(self):
        open_day(self.hub, DAY, 2)
        document = grant(self.hub, DAY, 'G1', BRANCH, 2)
        accept_grant(self.branch, document)
        hub_packet = read_handoff(self.hub, DAY)
        branch_packet = read_handoff(self.branch, DAY)
        self.assertEqual(read_handoff(self.hub, DAY), hub_packet)
        self.assertEqual((packet_position(self.hub), packet_position(self.branch)), (2, 1))
        returned = return_quota(self.branch, DAY, 'G1', 'R1', 1)
        settle_return(self.hub, returned.document)
        self.assertEqual((packet_position(self.hub), packet_position(self.branch)), (3, 2))
        result = audit_handoff(DAY, hub_packet, branch_packet)
        self.assertEqual((result['hub_position'], result['branch_position']), (2, 1))
        self.assertEqual(result['audit'].quota.pending_acceptance, 0)
        self.assertEqual(result['audit'].quota.pending_return, 0)

    def test_reopen_keeps_enable_state_and_event_order(self):
        open_day(self.hub, DAY, 2)
        document = grant(self.hub, DAY, 'G1', BRANCH, 2)
        accept_grant(self.branch, document)
        hub_packet = read_handoff(self.hub, DAY)
        branch_packet = read_handoff(self.branch, DAY)
        self.reopen_hub()
        self.reopen_branch()
        self.assertEqual(read_handoff(self.hub, DAY), hub_packet)
        self.assertEqual(read_handoff(self.branch, DAY), branch_packet)
        self.assertIsNone(enable_handoff(self.hub, DAY))
        returned = return_quota(self.branch, DAY, 'G1', 'R1', 1)
        settle_return(self.hub, returned.document)
        self.reopen_hub()
        self.reopen_branch()
        self.assertEqual(packet_kinds(self.hub), ['open', 'grant', 'settle'])
        self.assertEqual(packet_kinds(self.branch), ['accept', 'return'])
        result = audit_handoff(DAY, read_handoff(self.hub, DAY), read_handoff(self.branch, DAY))
        self.assertEqual((result['hub_position'], result['branch_position']), (3, 2))
        self.assertTrue(result['audit'].quota.balanced)

    def test_prefix_snapshots_match_live_reads(self):
        open_day(self.hub, DAY, 2)
        document = grant(self.hub, DAY, 'G1', BRANCH, 2)
        accept_grant(self.branch, document)
        returned = return_quota(self.branch, DAY, 'G1', 'R1', 1)
        stage(self.hub, DAY, 'empty', '总仓原表', [])
        publish_batch(self.hub, DAY, 'empty', 'Z')
        settle_return(self.hub, returned.document)
        stage(self.branch, DAY, 'b', '北岸补报原表', [(17, 7000, 'ml')])
        publish_batch(self.branch, DAY, 'b', 'B', 'G1')
        hub_packet = parse_handoff_packet(read_handoff(self.hub, DAY))
        branch_packet = parse_handoff_packet(read_handoff(self.branch, DAY))
        self.assertEqual(hub_snapshot_at(hub_packet, hub_packet.position),
                         read_day_snapshot(self.hub, DAY))
        self.assertEqual(branch_snapshot_at(branch_packet, branch_packet.position),
                         read_day_snapshot(self.branch, DAY))
        self.assertEqual(hub_snapshot_at(hub_packet, 1).budget.available, 2)
        self.assertEqual(hub_snapshot_at(hub_packet, 2).budget.available, 0)
        self.assertEqual(branch_snapshot_at(branch_packet, 1).accepted[0].available, 2)
        self.assertEqual(branch_snapshot_at(branch_packet, 2).accepted[0].available, 1)


class HandoffScriptTests(TestCase):
    def test_shift_audit_table_both_orderings(self):
        early_packets = run_handoff(True)
        late_packets = run_handoff(False)
        self.assertEqual(collect_handoff_rows(early_packets, late_packets),
                         expected_handoff_rows())


class HandoffRejectionTests(WarehouseCase):
    def _enabled_packets(self):
        enable_handoff(self.hub, DAY)
        enable_handoff(self.branch, DAY)
        open_day(self.hub, DAY, 2)
        document = grant(self.hub, DAY, 'G1', BRANCH, 2)
        accept_grant(self.branch, document)
        return read_handoff(self.hub, DAY), read_handoff(self.branch, DAY)

    def test_day_role_and_pairing_mismatch_rejected(self):
        hub_packet, branch_packet = self._enabled_packets()
        with self.assertRaises(ValueError):
            audit_handoff(NEXT_DAY, hub_packet, branch_packet)
        with self.assertRaises(ValueError):
            audit_handoff(DAY, branch_packet, hub_packet)
        with open_hub_store(':memory:', HUB, '南岸分仓', KEY) as other_hub, \
                open_branch_store(':memory:', '南岸分仓', HUB, KEY) as other_branch:
            enable_handoff(other_hub, DAY)
            enable_handoff(other_branch, DAY)
            open_day(other_hub, DAY, 1)
            other_hub_packet = read_handoff(other_hub, DAY)
            other_branch_packet = read_handoff(other_branch, DAY)
        with self.assertRaises(ValueError):
            audit_handoff(DAY, other_hub_packet, branch_packet)
        with self.assertRaises(ValueError):
            audit_handoff(DAY, hub_packet, other_branch_packet)

    def test_malformed_packets_rejected(self):
        hub_packet, branch_packet = self._enabled_packets()
        for bad in (b'', b'{}', 'text', 42, None,
                    hub_packet[:-3], hub_packet.replace(b'"hub"', b'"branch"')):
            with self.subTest(bad=repr(bad)[:40]), self.assertRaises(ValueError):
                audit_handoff(DAY, bad, branch_packet)
        with self.assertRaises(ValueError):
            audit_handoff(DAY, hub_packet, json.dumps({'format': 'shift-handoff/1'}).encode())

    def test_acceptance_requires_exact_issued_document(self):
        _, branch_packet = self._enabled_packets()
        with open_hub_store(':memory:', HUB, BRANCH, KEY) as alternate:
            enable_handoff(alternate, DAY)
            open_day(alternate, DAY, 5)
            grant(alternate, DAY, 'G1', BRANCH, 1)
            alternate_packet = read_handoff(alternate, DAY)
        result = audit_handoff(DAY, alternate_packet, branch_packet)
        self.assertEqual((result['hub_position'], result['branch_position']), (2, 0))
        line = result['audit'].quota.grants[0]
        self.assertEqual((line.grant.rows, line.pending_acceptance), (1, 1))
        self.assertEqual(result['audit'].quota.branch_available, 0)
        self.assertTrue(result['audit'].quota.balanced)
