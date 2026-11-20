"""共享网关站点：连接还原、端口占用、共享域许可与证据、复验影响。"""
import copy
import json
import unittest

from _util import DATA, load_module

site_mod = load_module("site")


def load_site():
    return site_mod.SharedSite.load(DATA)


class ConnectionResolutionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = load_site()
        cls.report = cls.site.analyze()

    def test_now_is_latest_receipt(self):
        self.assertEqual(self.site.now.isoformat(), "2026-08-15T11:00:00+08:00")

    def test_connections_follow_receipts_and_census(self):
        conn = self.report["connections"]
        self.assertEqual(conn["U41"]["gateway"], "GW-T")   # 回执确认迁出
        self.assertEqual(conn["U42"]["gateway"], "GW-A")   # 回执确认未迁
        self.assertEqual(conn["U43"]["gateway"], "GW-A")   # 盘点发现仍在原网关
        self.assertTrue(all(c["status"] == "confirmed" for c in conn.values()))

    def test_standby_gateway_ports_stay_occupied(self):
        gateways = self.report["gateways"]
        self.assertEqual(gateways["GW-T"]["used"], 1)      # U41 持续占用
        self.assertEqual(gateways["GW-A"]["used"], 2)      # U42 + U43

    def test_commitment_gap_detected_for_discovered_home(self):
        homes = self.report["homes"]
        self.assertFalse(homes["U41"]["commitment_gap"])
        self.assertFalse(homes["U42"]["commitment_gap"])
        self.assertTrue(homes["U43"]["commitment_gap"])
        self.assertEqual(homes["U43"]["commitments"]["ring"]["status"], "unobserved")
        self.assertEqual(homes["U43"]["commitments"]["local_alert"]["status"], "unobserved")

    def test_batches(self):
        batches = self.report["batches"]
        self.assertEqual(batches["upgradeable"], [])
        self.assertEqual(batches["mid_transition"], ["U41"])
        self.assertEqual(batches["commitment_gaps"], ["U43"])


class SharedDomainOpsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = load_site()
        cls.report = cls.site.analyze()

    def _op(self, op_id, home, window):
        for v in self.report["candidate_operations"]:
            if v["operation"] == op_id and v["home"] == home and v["window"] == window:
                return v
        raise AssertionError(f"missing {op_id} {home} {window}")

    def test_gateway_upgrade_needs_all_affected_parties(self):
        verdict = self._op("OP3", None, "PW1")
        self.assertFalse(verdict["executable"])
        permission = next(b for b in verdict["blockers"] if b["type"] == "permission")
        parties = {g["party"] for g in permission["gaps"]}
        # U43 未授权 upgrade_gateway：一户同意不能当整楼同意
        self.assertIn("U43", parties)
        pw2 = self._op("OP3", None, "PW2")
        parties2 = {
            g["party"]
            for b in pw2["blockers"] if b["type"] == "permission"
            for g in b["gaps"]
        }
        self.assertIn("U42", parties2)   # U42 在 PW2 没有 upgrade_gateway 许可
        self.assertIn("U43", parties2)

    def test_single_home_trial_does_not_cover_two_home_temporary(self):
        # 单户实验不能直接支持多户同时接入
        verdict = self._op("OP1", "U42", "PW1")
        self.assertFalse(verdict["executable"])
        evidence = next(b for b in verdict["blockers"] if b["type"] == "evidence")
        homes = {g["home"] for g in evidence["gaps"]}
        self.assertIn("U42", homes)
        self.assertIn("U41", homes)      # 备机上会变成 2 户同时接入
        for gap in evidence["gaps"]:
            self.assertEqual(gap["scope"]["households_connected"], 2)
            self.assertTrue(gap["scope"]["temporary"])

    def test_u43_permission_limits_are_named(self):
        verdict = self._op("OP1", "U43", "PW1")
        permission = next(b for b in verdict["blockers"] if b["type"] == "permission")
        self.assertEqual({g["party"] for g in permission["gaps"]}, {"U43"})

    def test_recheck_rr41_unblocks_parking_but_not_upgrade(self):
        rechecks = {r["id"]: r for r in self.site.recheck_report()}
        rr41_new = {(n["operation"], n["home"]) for n in rechecks["RR41"]["newly_executable"]}
        self.assertIn(("OP1", "U42"), rr41_new)
        # RR42 补的是升级后范围，但许可与另一阶段证据仍缺
        self.assertEqual(rechecks["RR42"]["newly_executable"], [])

    def test_permission_grants_alone_unblock_nothing(self):
        for what_if in self.site.permission_what_if():
            self.assertEqual(what_if["newly_executable"], [])


class PendingOrderTest(unittest.TestCase):
    def test_unreceipted_order_holds_both_ends_and_blocks_dependents(self):
        raw = json.loads((DATA / "shared_gateway.json").read_text(encoding="utf-8"))
        # 去掉 WO42 的回执：迁接结果未核实
        raw["receipts"] = [r for r in raw["receipts"] if r["work_order"] != "WO42"]
        service = json.loads((DATA / "maintenance_windows.json").read_text(encoding="utf-8"))
        protocol = {
            name: site_mod.FunctionProtocol.from_record(rec)
            for name, rec in service["protocol"].items()
        }
        site = site_mod.SharedSite(raw, protocol)
        report = site.analyze()
        self.assertEqual(report["connections"]["U42"]["status"], "pending")
        ports = report["gateways"]
        self.assertEqual(ports["GW-A"]["held"], 1)   # 两端端口都保留
        self.assertEqual(ports["GW-T"]["held"], 1)
        # 依赖 U42 连接的下一步不能承诺
        u42_ops = [
            v for v in report["candidate_operations"]
            if v["home"] == "U42" or "U42" in v["affected"]
        ]
        self.assertTrue(u42_ops)
        for v in u42_ops:
            self.assertFalse(v["executable"])
            self.assertIn("unverified", {b["type"] for b in v["blockers"]})


class JointRecheckCoverageTest(unittest.TestCase):
    def test_groups_by_scope_and_checks_window_readiness(self):
        site = load_site()
        groups = site_mod.joint_recheck_coverage([site])
        self.assertEqual(len(groups), 2)
        by_recheck = {
            s["recheck"]: s for g in groups for s in g["sites"]
        }
        self.assertTrue(by_recheck["RR41"]["window_open"])
        self.assertTrue(by_recheck["RR42"]["window_open"])
        # 联合覆盖不放大单站结论：RR41 放行 U42 暂挂，RR42 不放行任何操作
        self.assertTrue(by_recheck["RR41"]["newly_executable"])
        self.assertEqual(by_recheck["RR42"]["newly_executable"], [])


class SiteBundleTest(unittest.TestCase):
    def test_pb43_goes_to_manual_support(self):
        site = load_site()
        report = site.analyze()
        view = site.bundle_view(report, "PB43")
        self.assertTrue(view["manual_support"])
        self.assertEqual(view["cited_trials"], [])

    def test_pb41_still_fulfilled_on_standby(self):
        site = load_site()
        report = site.analyze()
        view = site.bundle_view(report, "PB41")
        self.assertFalse(view["manual_support"])
        self.assertEqual(len(view["cited_trials"]), 2)
        self.assertEqual(view["current"]["gateway"], "GW-T")


if __name__ == "__main__":
    unittest.main()
