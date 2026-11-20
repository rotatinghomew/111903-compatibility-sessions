"""维护窗口数据集：路径、容量、预留释放、证据下调后的处置。"""
import copy
import json
import unittest

from _util import DATA, load_module

maintenance = load_module("maintenance")


def load_data():
    return maintenance.MaintenanceData.load(DATA)


class FixtureBatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_data()
        cls.batch = maintenance.analyze_households(cls.data)

    def test_now_is_latest_known_event(self):
        self.assertEqual(self.data.now.isoformat(), "2026-08-09T13:00:00+08:00")

    def test_executed_dispatch_is_current_truth(self):
        # 已完成的步骤当作家庭真实现状
        self.assertEqual(self.data.current_state("H11"), "S1")
        self.assertEqual(self.data.current_state("H13"), "S0")

    def test_correction_downgrades_intermediate_state(self):
        status = self.data.support("ring", "S1", "legacy_router")
        self.assertEqual(status.status, "unobserved")
        self.assertEqual(status.complete_installations, 1)
        moved = self.data.support("ring", "S1", "new_router")
        self.assertEqual(moved.complete_installations, 1)

    def test_h11_advances_in_w1_with_retreat_option(self):
        h11 = self.batch["households"]["H11"]
        self.assertEqual(h11["status"], "advance")
        self.assertTrue(h11["at_risk"])
        steps = h11["plan"]["steps"]
        self.assertEqual([(s["from"], s["to"], s["window"]) for s in steps], [("S1", "S3", "W1")])
        # 安全退回只限 W1（last_window），不推荐使用不到的窗口
        retreats = h11["retreat_options"]
        self.assertEqual(len(retreats), 1)
        self.assertEqual(retreats[0]["steps"][0]["to"], "S0")
        self.assertEqual(retreats[0]["steps"][0]["window"], "W1")

    def test_h13_replans_without_loaner_and_releases_reservation(self):
        h13 = self.batch["households"]["H13"]
        self.assertEqual(h13["status"], "advance")
        steps = h13["plan"]["steps"]
        self.assertEqual(
            [(s["from"], s["to"]) for s in steps], [("S0", "S2"), ("S2", "S3")]
        )
        self.assertTrue(all(s["window"] == "W2" for s in steps))
        # 原预留方案 35+30=65 分钟超过 W2 的 60 分钟，显式释放回公用池
        self.assertEqual(h13["plan"]["releases"], ["D13"])

    def test_h12_keeps_original_for_missing_evidence(self):
        h12 = self.batch["households"]["H12"]
        self.assertEqual(h12["status"], "blocked")
        self.assertEqual(h12["blocker"], "stage_evidence_missing")
        self.assertEqual(h12["current_state"], "S0")

    def test_window_capacity_after_allocation(self):
        windows = self.batch["windows"]
        self.assertEqual(windows["W1"]["remaining"], 40)   # 90 - 20(已执行) - 30(H11)
        self.assertEqual(windows["W2"]["remaining"], 10)   # 60 - 50(H13)

    def test_retrial_slot_maps_to_gap(self):
        slot = self.batch["retrial_slots"][0]
        self.assertEqual(slot["id"], "Q1")
        restored = {(r["function"], r["scope"]["state"]) for r in slot["would_restore"]}
        self.assertIn(("ring", "S1"), restored)


class CapacityConflictTest(unittest.TestCase):
    def test_capacity_shortage_is_distinguished(self):
        data = load_data()
        # W2 工时缩到 40：H13 的 50 分钟路径证据可行但容量不足
        data.service["windows"]["W2"]["minutes"] = 40
        data.windows["W2"].minutes = 40
        batch = maintenance.analyze_households(data)
        h13 = batch["households"]["H13"]
        self.assertEqual(h13["status"], "blocked")
        self.assertEqual(h13["blocker"], "capacity_insufficient")
        # H11 走 W1，不受 W2 压缩影响
        self.assertEqual(batch["households"]["H11"]["status"], "advance")


class EquipmentTest(unittest.TestCase):
    def test_reserved_loaner_held_until_explicit_release(self):
        data = load_data()
        capacity = maintenance._Capacity.fresh(data)
        unit = data.equipment[0]
        self.assertFalse(capacity.equipment_free(unit, "W2"))
        capacity.release_reservation("D13")
        self.assertTrue(capacity.equipment_free(unit, "W2"))

    def test_loaner_stays_with_household_in_temporary_state(self):
        data = load_data()
        planner = maintenance.HouseholdPlanner(data)
        hh = data.households["H13"]
        edges = [e for e in data.edges_for("H13") if e.frm == "S0" and e.to == "SB"]
        capacity = maintenance._Capacity.fresh(data)
        # 预留未显式释放前，替换件仍被 D13 占用，排程不能使用
        self.assertIsNone(planner.schedule(hh, edges, capacity))
        capacity.release_reservation("D13")
        steps = planner.schedule(hh, edges, capacity)
        self.assertIsNotNone(steps)
        holds = planner._equipment_holds(hh, edges, steps)
        self.assertEqual(holds, ["loaner_bell"])

    def test_keeping_reservation_consumes_earmark_once(self):
        data = load_data()
        # 让预留方案 S0→SB→S3 成为唯一可行路径：去掉 S0→S2、S2→S3，W2 放宽到 90
        data.service["changes"] = [
            c for c in data.service["changes"]
            if (c["from"], c["to"]) not in {("S0", "S2"), ("S2", "S3")}
        ]
        data.service["windows"]["W2"]["minutes"] = 90
        data.windows["W2"].minutes = 90
        batch = maintenance.analyze_households(data)
        h13 = batch["households"]["H13"]
        self.assertEqual(h13["status"], "advance")
        plan = h13["plan"]
        self.assertEqual(plan["consumes"], ["D13"])
        self.assertEqual(plan["releases"], [])
        self.assertEqual(plan["equipment_holds"], [])  # 计划内离开临时状态
        # 90 - 35(预留承接首步) - 30(第二步) = 25，预留工时不重复扣
        self.assertEqual(batch["windows"]["W2"]["remaining"], 25)


class BundleViewTest(unittest.TestCase):
    def test_sb11_contract_then_correction_then_options(self):
        data = load_data()
        batch = maintenance.analyze_households(data)
        view = maintenance.bundle_view(data, batch, "SB11")
        # 当时的承诺：计划各站均有支持
        self.assertTrue(all(p["status"] == "supported" for p in view["promised_support"]))
        # 签约后的更正
        self.assertEqual([c["id"] for c in view["corrections_since"]], ["C1"])
        # 已执行步骤
        self.assertEqual([s["id"] for s in view["executed_steps"]], ["D11"])
        # 现在仍可兑现（前进 S1→S3）
        self.assertTrue(view["fulfillable_now"])
        self.assertFalse(view["manual_support"])


if __name__ == "__main__":
    unittest.main()
