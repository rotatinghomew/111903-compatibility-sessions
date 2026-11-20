"""证据模型：独立安装计数、未观察/失败/支持、更正另发新范围。"""
import unittest

from _util import load_module

evidence = load_module("evidence")

PROTO = {
    "ring": evidence.FunctionProtocol(stages=("pair", "reconnect"), min_complete_installations=2),
    "local_alert": evidence.FunctionProtocol(stages=("reconnect",), min_complete_installations=2),
}
SCOPE_KEYS = ("state", "topology")


def trial(tid, inst, state="S1", topo="legacy", function="ring", pair="pass", reconnect="pass"):
    return {
        "id": tid,
        "installation": inst,
        "function": function,
        "scope": {"state": state, "topology": topo},
        "stages": {"pair": pair, "reconnect": reconnect},
    }


class DistinctInstallationTest(unittest.TestCase):
    def test_same_installation_repeated_binding_counts_once(self):
        # 同一安装反复通过绑定不算多户通过
        base = evidence.EvidenceBase(
            PROTO,
            [trial("T1", "I1"), trial("T2", "I1"), trial("T3", "I1")],
            [],
            SCOPE_KEYS,
        )
        status = base.status("ring", state="S1", topology="legacy")
        self.assertEqual(status.observed_installations, 1)
        self.assertEqual(status.complete_installations, 1)
        self.assertEqual(status.status, evidence.UNOBSERVED)

    def test_two_installations_reach_threshold(self):
        base = evidence.EvidenceBase(
            PROTO, [trial("T1", "I1"), trial("T2", "I2")], [], SCOPE_KEYS
        )
        status = base.status("ring", state="S1", topology="legacy")
        self.assertEqual(status.status, evidence.SUPPORTED)
        self.assertEqual(status.complete_installations, 2)

    def test_null_stage_is_unobserved_not_failure(self):
        base = evidence.EvidenceBase(
            PROTO,
            [trial("T1", "I1", reconnect=None), trial("T2", "I2", reconnect=None)],
            [],
            SCOPE_KEYS,
        )
        status = base.status("ring", state="S1", topology="legacy")
        self.assertEqual(status.status, evidence.UNOBSERVED)
        self.assertEqual(status.stage_rollup["reconnect"], "unobserved")
        self.assertEqual(status.stage_rollup["pair"], "pass")

    def test_confirmed_failure_dominates(self):
        base = evidence.EvidenceBase(
            PROTO,
            [trial("T1", "I1"), trial("T2", "I2"), trial("T3", "I3", reconnect="fail")],
            [],
            SCOPE_KEYS,
        )
        status = base.status("ring", state="S1", topology="legacy")
        self.assertEqual(status.status, evidence.FAILED)
        self.assertEqual(status.failed_installations, 1)

    def test_no_cross_topology_inheritance(self):
        base = evidence.EvidenceBase(
            PROTO, [trial("T1", "I1"), trial("T2", "I2")], [], SCOPE_KEYS
        )
        other = base.status("ring", state="S1", topology="new_router")
        self.assertEqual(other.status, evidence.UNOBSERVED)
        self.assertEqual(other.observed_installations, 0)


class CorrectionTest(unittest.TestCase):
    def test_correction_issues_new_scope_without_rewriting_original(self):
        corrections = [
            {
                "id": "C1",
                "trial": "T2",
                "known_at": "2026-08-09T13:00:00+08:00",
                "changed_fields": {"topology": "new_router"},
            }
        ]
        base = evidence.EvidenceBase(
            PROTO, [trial("T1", "I1"), trial("T2", "I2")], corrections, SCOPE_KEYS
        )
        old_scope = base.status("ring", state="S1", topology="legacy")
        new_scope = base.status("ring", state="S1", topology="new_router")
        # 原范围支持被收回，新范围另发
        self.assertEqual(old_scope.complete_installations, 1)
        self.assertEqual(old_scope.status, evidence.UNOBSERVED)
        self.assertEqual(new_scope.complete_installations, 1)
        # 原始交付记录不被改写
        raw = next(t for t in base._raw_trials if t["id"] == "T2")
        self.assertEqual(raw["scope"]["topology"], "legacy")
        self.assertTrue(any("转出" in n for n in old_scope.notes))
        self.assertTrue(any("转入" in n for n in new_scope.notes))

    def test_snapshot_before_correction_keeps_old_support(self):
        corrections = [
            {
                "id": "C1",
                "trial": "T2",
                "known_at": "2026-08-09T13:00:00+08:00",
                "changed_fields": {"topology": "new_router"},
            }
        ]
        moment = evidence.parse_time("2026-08-08T10:00:00+08:00")
        base = evidence.EvidenceBase(
            PROTO, [trial("T1", "I1"), trial("T2", "I2")], corrections, SCOPE_KEYS, at=moment
        )
        status = base.status("ring", state="S1", topology="legacy")
        self.assertEqual(status.status, evidence.SUPPORTED)

    def test_stage_correction_can_register_failure(self):
        corrections = [
            {
                "id": "C2",
                "trial": "T1",
                "known_at": "2026-08-09T13:00:00+08:00",
                "changed_fields": {"reconnect": "fail"},
            }
        ]
        base = evidence.EvidenceBase(
            PROTO, [trial("T1", "I1"), trial("T2", "I2")], corrections, SCOPE_KEYS
        )
        status = base.status("ring", state="S1", topology="legacy")
        self.assertEqual(status.status, evidence.FAILED)


if __name__ == "__main__":
    unittest.main()
