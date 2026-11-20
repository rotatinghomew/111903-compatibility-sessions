"""维护窗口数据集：家庭升级路径、容量与放量批次。

口径：
- 已执行的步骤是家庭的真实现状，不因分析被撤销而回到最初版本。
- 已预留未开始的安排占用窗口工时与替换件，只有显式释放才回到公用池。
- 每个中间组合都要支持承诺中不能中断的功能；允许在窗口内中断的
  功能必须在窗口关门前恢复。
- 替换件库存按实物统一计算，不按产品线分开计数。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .evidence import (
    EvidenceBase,
    FunctionProtocol,
    ScopeStatus,
    normalize_trial,
    parse_time,
    SUPPORTED,
    UNOBSERVED,
    FAILED,
)

SCOPE_KEYS = ("state", "topology")

# 阻塞分类
BLOCK_STAGE_EVIDENCE = "stage_evidence_missing"   # 某个中间阶段缺证
BLOCK_NO_TRANSITION = "no_feasible_transition"    # 当前已无可行转换
BLOCK_CAPACITY = "capacity_insufficient"          # 有路径但伙伴容量不足


@dataclass(frozen=True)
class Edge:
    frm: str
    to: str
    minutes: int
    equipment: str | None = None
    kind: str = "change"          # change | reversible
    last_window: str | None = None


@dataclass
class Window:
    id: str
    minutes: int
    crew: int
    loaner_bell: int
    start: datetime | None = None
    end: datetime | None = None

    @property
    def capacity(self) -> int:
        return self.minutes * self.crew


@dataclass
class Household:
    id: str
    topology: str
    start: str
    goal: str
    continuous: list[str]
    maintenance_only_outage: list[str]
    windows: list[str]
    priority: int


@dataclass
class Step:
    edge: Edge
    window: str


@dataclass
class Plan:
    household: str
    states: list[str]
    steps: list[Step]
    releases: list[str] = field(default_factory=list)
    consumes: list[str] = field(default_factory=list)
    equipment_holds: list[str] = field(default_factory=list)

    @property
    def total_minutes(self) -> int:
        return sum(s.edge.minutes for s in self.steps)

    def to_dict(self) -> dict:
        return {
            "household": self.household,
            "states": list(self.states),
            "steps": [
                {
                    "from": s.edge.frm,
                    "to": s.edge.to,
                    "minutes": s.edge.minutes,
                    "window": s.window,
                    "equipment": s.edge.equipment,
                    "kind": s.edge.kind,
                }
                for s in self.steps
            ],
            "releases": list(self.releases),
            "consumes": list(self.consumes),
            "equipment_holds": list(self.equipment_holds),
            "total_minutes": self.total_minutes,
        }


class MaintenanceData:
    """maintenance_windows.json + partner_calendar.json 的分析视图。"""

    def __init__(self, service: dict, partner: dict) -> None:
        self.service = service
        self.partner = partner
        self.protocol = {
            name: FunctionProtocol.from_record(rec)
            for name, rec in service["protocol"].items()
        }
        stage_keys = tuple(
            sorted({s for p in self.protocol.values() for s in p.stages})
        )
        trials = [normalize_trial(t, SCOPE_KEYS, stage_keys) for t in service["trials"]]
        trials += [
            normalize_trial(t, SCOPE_KEYS, stage_keys)
            for t in partner.get("additional_trials", [])
        ]
        self.corrections = list(service.get("corrections", []))
        self.evidence = EvidenceBase(
            self.protocol, trials, self.corrections, SCOPE_KEYS
        )
        self.states = dict(service["states"])
        self.windows: dict[str, Window] = {}
        calendar_windows = {w["id"]: w for w in partner.get("windows", [])}
        for wid, rec in service["windows"].items():
            cal = calendar_windows.get(wid, {})
            self.windows[wid] = Window(
                id=wid,
                minutes=rec["minutes"],
                crew=rec["crew"],
                loaner_bell=rec.get("loaner_bell", 0),
                start=parse_time(cal["start"]) if "start" in cal else None,
                end=parse_time(cal["end"]) if "end" in cal else None,
            )
        self.households: dict[str, Household] = {
            h["id"]: Household(
                id=h["id"],
                topology=h["topology"],
                start=h["start"],
                goal=h["goal"],
                continuous=list(h["continuous"]),
                maintenance_only_outage=list(h.get("maintenance_only_outage", [])),
                windows=list(h["windows"]),
                priority=h["priority"],
            )
            for h in service["households"]
        }
        self.dispatches = list(service.get("dispatches", []))
        self.equipment = list(partner.get("equipment", []))
        self.equipment_bookings = list(partner.get("equipment_bookings", []))
        self.work_log = list(partner.get("work_log", []))
        self.reversible = list(partner.get("allowed_reversible_transitions", []))
        self.retrial_slots = list(service.get("retrial_slots", []))
        self.sold_bundles = list(partner.get("sold_bundles", []))
        self.now = self._derive_now()

    @classmethod
    def load(cls, root: str | Path) -> "MaintenanceData":
        root = Path(root)
        service = json.loads((root / "maintenance_windows.json").read_text(encoding="utf-8"))
        partner = json.loads((root / "partner_calendar.json").read_text(encoding="utf-8"))
        return cls(service, partner)

    def _derive_now(self) -> datetime:
        stamps: list[datetime] = []
        for corr in self.corrections:
            stamps.append(parse_time(corr["known_at"]))
        for log in self.work_log:
            stamps.append(parse_time(log["started"]))
            stamps.append(parse_time(log["ended"]))
        if not stamps:
            ends = [w.end for w in self.windows.values() if w.end]
            if ends:
                return min(ends)
            return datetime.max.replace(tzinfo=parse_time("2000-01-01T00:00:00+00:00").tzinfo)
        return max(stamps)

    # ------------------------------------------------------------------
    def evidence_at(self, moment: datetime) -> EvidenceBase:
        """某一时点可用的证据视图（只套用当时已知悉的更正）。"""
        stage_keys = tuple(
            sorted({s for p in self.protocol.values() for s in p.stages})
        )
        trials = [normalize_trial(t, SCOPE_KEYS, stage_keys) for t in self.service["trials"]]
        trials += [
            normalize_trial(t, SCOPE_KEYS, stage_keys)
            for t in self.partner.get("additional_trials", [])
        ]
        return EvidenceBase(self.protocol, trials, self.corrections, SCOPE_KEYS, at=moment)

    def window_open(self, window: Window) -> bool:
        return window.end is None or window.end > self.now

    def executed_minutes(self, window_id: str) -> int:
        return sum(
            d.get("minutes_used", 0)
            for d in self.dispatches
            if d["window"] == window_id and d["status"] == "executed"
        )

    def change_minutes(self, frm: str, to: str) -> int | None:
        for c in self.service["changes"]:
            if c["from"] == frm and c["to"] == to:
                return c["minutes"]
        return None

    def current_state(self, household_id: str) -> str:
        """已执行步骤之后的真实现状。"""
        hh = self.households[household_id]
        done = [d for d in self.dispatches if d["household"] == household_id and d["status"] == "executed"]

        def order_key(d: dict):
            w = self.windows[d["window"]]
            return (w.start or datetime.min.replace(tzinfo=self.now.tzinfo), d["id"])

        state = hh.start
        for d in sorted(done, key=order_key):
            state = d["to"]
        return state

    def edges_for(self, household_id: str) -> list[Edge]:
        edges = [
            Edge(c["from"], c["to"], c["minutes"], c.get("equipment"), "change")
            for c in self.service["changes"]
        ]
        for r in self.reversible:
            if r["household"] == household_id:
                edges.append(
                    Edge(r["start"], r["end"], r["minutes"], None, "reversible", r["last_window"])
                )
        return edges

    # ------------------------------------------------------------------
    def support(self, function: str, state: str, topology: str) -> ScopeStatus:
        return self.evidence.status(function, state=state, topology=topology)


@dataclass
class _Capacity:
    """批次分配过程中的共享容量视图。"""

    data: MaintenanceData
    remaining: dict[str, int] = field(default_factory=dict)
    # unit_id -> 已占用窗口列表（含预约与已分配的新计划）
    equipment_busy: dict[str, list[str]] = field(default_factory=dict)
    reserved_earmark: dict[str, int] = field(default_factory=dict)

    @classmethod
    def fresh(cls, data: MaintenanceData) -> "_Capacity":
        cap = cls(data)
        for wid, w in data.windows.items():
            cap.remaining[wid] = w.capacity - data.executed_minutes(wid)
        for d in data.dispatches:
            if d["status"] != "reserved":
                continue
            minutes = data.change_minutes(d["from"], d["to"]) or 0
            cap.reserved_earmark[d["id"]] = minutes
            cap.remaining[d["window"]] = cap.remaining.get(d["window"], 0) - minutes
            if d.get("equipment"):
                for booking in data.equipment_bookings:
                    if booking["reservation"] == d["id"]:
                        cap.equipment_busy.setdefault(booking["equipment"], []).append(
                            booking["from_window"]
                        )
        return cap

    def release_reservation(self, dispatch_id: str) -> None:
        """显式释放：预留的工时与替换件回到公用池。"""
        d = next((x for x in self.data.dispatches if x["id"] == dispatch_id), None)
        if d is None or d["status"] != "reserved":
            return
        minutes = self.reserved_earmark.pop(dispatch_id, 0)
        self.remaining[d["window"]] = self.remaining.get(d["window"], 0) + minutes
        for booking in self.data.equipment_bookings:
            if booking["reservation"] == dispatch_id:
                busy = self.equipment_busy.get(booking["equipment"], [])
                if booking["from_window"] in busy:
                    busy.remove(booking["from_window"])

    def consume_reservation(self, dispatch_id: str) -> None:
        """预留被该户自己的计划沿用：工时与替换件由计划承接，不回池。"""
        self.reserved_earmark.pop(dispatch_id, None)

    def equipment_free(self, unit: dict, window_id: str) -> bool:
        if window_id not in unit.get("possible_windows", []):
            return False
        window = self.data.windows[window_id]
        for booking in self.data.equipment_bookings:
            if booking["equipment"] != unit["id"]:
                continue
            if booking["reservation"] not in self._active_reservations():
                continue
            from_start = self.data.windows[booking["from_window"]].start
            release = parse_time(booking["earliest_release"])
            if window.start and window.end and window.start < release and (from_start is None or window.end > from_start):
                return False
        return window_id not in self.equipment_busy.get(unit["id"], [])

    def _active_reservations(self) -> set[str]:
        return set(self.reserved_earmark)


class HouseholdPlanner:
    """单户路径枚举与窗口排程。"""

    def __init__(self, data: MaintenanceData) -> None:
        self.data = data

    # ------------------------------------------------------------------
    def enumerate_paths(self, hh: Household, max_depth: int = 5) -> list[list[Edge]]:
        start = self.data.current_state(hh.id)
        edges = self.data.edges_for(hh.id)
        paths: list[list[Edge]] = []
        frontier: list[tuple[str, list[Edge], set[str]]] = [(start, [], {start})]
        while frontier:
            state, used, seen = frontier.pop()
            if state == hh.goal and used:
                paths.append(used)
                continue
            if len(used) >= max_depth:
                continue
            for edge in edges:
                if edge.frm != state or edge.to in seen:
                    continue
                frontier.append((edge.to, used + [edge], seen | {edge.to}))
        return paths

    def path_evidence_gaps(self, hh: Household, states: list[str]) -> list[ScopeStatus]:
        gaps: list[ScopeStatus] = []
        for state in states:
            for function in hh.continuous:
                status = self.data.support(function, state, hh.topology)
                if not status.ok:
                    gaps.append(status)
        return gaps

    def window_end_gaps(
        self, hh: Household, states: list[str], steps: list[Step]
    ) -> list[ScopeStatus]:
        """允许窗口内中断的功能，必须在每个窗口关门前恢复。"""
        if not hh.maintenance_only_outage:
            return []
        gaps: list[ScopeStatus] = []
        # 每个窗口最后到达的状态
        last_state_by_window: dict[str, str] = {}
        for step, state in zip(steps, states[1:]):
            last_state_by_window[step.window] = state
        for state in last_state_by_window.values():
            for function in hh.maintenance_only_outage:
                status = self.data.support(function, state, hh.topology)
                if not status.ok:
                    gaps.append(status)
        return gaps

    # ------------------------------------------------------------------
    def schedule(
        self,
        hh: Household,
        edges: list[Edge],
        capacity: _Capacity,
        ignore_capacity: bool = False,
    ) -> list[Step] | None:
        windows = sorted(
            (self.data.windows[w] for w in hh.windows),
            key=lambda w: w.start or datetime.min.replace(tzinfo=self.data.now.tzinfo),
        )
        usage: dict[str, int] = {w.id: 0 for w in windows}
        loaner_used: dict[str, int] = {w.id: 0 for w in windows}
        steps: list[Step] = []
        for edge in edges:
            placed = False
            for window in windows:
                if not self.data.window_open(window):
                    continue
                if edge.kind == "reversible" and edge.last_window:
                    last = self.data.windows[edge.last_window]
                    if window.start and last.start and window.start > last.start:
                        continue
                if not ignore_capacity:
                    if usage[window.id] + edge.minutes > capacity.remaining.get(window.id, 0):
                        continue
                    if edge.equipment:
                        if loaner_used[window.id] + 1 > window.loaner_bell:
                            continue
                        unit = self._find_unit(edge.equipment, window.id, capacity)
                        if unit is None:
                            continue
                steps.append(Step(edge=edge, window=window.id))
                usage[window.id] += edge.minutes
                if edge.equipment:
                    loaner_used[window.id] += 1
                placed = True
                break
            if not placed:
                return None
        return steps

    def _find_unit(self, kind: str, window_id: str, capacity: _Capacity) -> dict | None:
        for unit in self.data.equipment:
            if unit["kind"] == kind and capacity.equipment_free(unit, window_id):
                return unit
        return None

    # ------------------------------------------------------------------
    def feasible_plans(
        self, hh: Household, capacity: _Capacity, ignore_capacity: bool = False
    ) -> list[Plan]:
        plans: list[Plan] = []
        current = self.data.current_state(hh.id)
        for edges in self.enumerate_paths(hh):
            states = [current] + [e.to for e in edges]
            # 当前状态是既成事实（单独用 at_risk 标注）；计划进入的每个
            # 中间与最终组合都必须支持持续承诺的功能。
            if self.path_evidence_gaps(hh, states[1:]):
                continue
            own_reserved = [
                d for d in self.data.dispatches
                if d["household"] == hh.id and d["status"] == "reserved"
            ]
            # 本户自己的预留先回到规划视图（沿用=承接，不用=显式释放）
            view = self._with_releases(capacity, own_reserved)
            steps = self.schedule(hh, edges, view, ignore_capacity=ignore_capacity)
            if steps is None:
                continue
            if self.window_end_gaps(hh, states, steps):
                continue
            consumes = [
                d["id"] for d in own_reserved
                if edges[0].frm == d["from"] and edges[0].to == d["to"]
                and steps[0].window == d["window"]
            ]
            releases = [d["id"] for d in own_reserved if d["id"] not in consumes]
            holds = self._equipment_holds(hh, edges, steps)
            plans.append(
                Plan(household=hh.id, states=states, steps=steps, releases=releases,
                     consumes=consumes, equipment_holds=holds)
            )
        plans.sort(key=lambda p: (len(p.steps), p.total_minutes, p.states))
        return plans

    def _with_releases(self, capacity: _Capacity, reserved: list[dict]) -> _Capacity:
        import copy

        view = copy.deepcopy(capacity)
        for d in reserved:
            view.release_reservation(d["id"])
        return view

    def _equipment_holds(self, hh: Household, edges: list[Edge], steps: list[Step]) -> list[str]:
        """计划结束时仍停留在临时状态，替换件随户保留。"""
        holds: list[str] = []
        temp_states = {
            name for name, parts in self.data.states.items()
            if any(str(v) == "loaner" for v in parts.values())
        }
        if edges and edges[-1].to in temp_states:
            for step in steps:
                if step.edge.equipment:
                    holds.append(step.edge.equipment)
        return holds

    # ------------------------------------------------------------------
    def retreat_options(self, hh: Household, capacity: _Capacity) -> list[Plan]:
        """从实际中间态可做的安全退回（可逆变更，目标状态证据足够）。"""
        current = self.data.current_state(hh.id)
        options: list[Plan] = []
        for edge in self.data.edges_for(hh.id):
            if edge.frm != current or edge.kind != "reversible":
                continue
            if self.path_evidence_gaps(hh, [edge.to]):
                continue
            steps = self.schedule(hh, [edge], capacity)
            if steps:
                options.append(Plan(household=hh.id, states=[current, edge.to], steps=steps))
        return options


# ----------------------------------------------------------------------
def analyze_households(data: MaintenanceData) -> dict:
    """按服务优先级分配共享容量，给出每户的放量结论。"""
    planner = HouseholdPlanner(data)
    capacity = _Capacity.fresh(data)

    def at_risk(hh: Household) -> bool:
        current = data.current_state(hh.id)
        return any(
            not data.support(f, current, hh.topology).ok for f in hh.continuous
        )

    ordered = sorted(
        data.households.values(),
        key=lambda h: (h.priority, not at_risk(h), h.id),
    )

    results: dict[str, dict] = {}
    for hh in ordered:
        current = data.current_state(hh.id)
        current_support = {
            f: data.support(f, current, hh.topology) for f in hh.continuous
        }
        entry: dict = {
            "household": hh.id,
            "priority": hh.priority,
            "topology": hh.topology,
            "current_state": current,
            "goal": hh.goal,
            "current_support": {f: s.to_dict() for f, s in current_support.items()},
            "at_risk": any(not s.ok for s in current_support.values()),
        }
        if current == hh.goal:
            entry["status"] = "at_goal"
            results[hh.id] = entry
            continue

        plans = planner.feasible_plans(hh, capacity)
        if plans:
            chosen = plans[0]
            for release in chosen.releases:
                capacity.release_reservation(release)
            consumed = set(chosen.consumes)
            for cid in consumed:
                capacity.consume_reservation(cid)
            for index, step in enumerate(chosen.steps):
                # 沿用预留的首步：工时与替换件已由预留登记覆盖
                if index == 0 and consumed:
                    continue
                capacity.remaining[step.window] -= step.edge.minutes
                if step.edge.equipment:
                    unit = planner._find_unit(step.edge.equipment, step.window, capacity)
                    if unit:
                        capacity.equipment_busy.setdefault(unit["id"], []).append(step.window)
            entry["status"] = "advance"
            entry["plan"] = chosen.to_dict()
            retreats = planner.retreat_options(hh, capacity)
            if retreats:
                entry["retreat_options"] = [r.to_dict() for r in retreats]
            results[hh.id] = entry
            continue

        # 无可行计划：区分缺证、无可行转换、容量不足
        unlimited = planner.feasible_plans(hh, capacity, ignore_capacity=True)
        if unlimited:
            entry["status"] = "blocked"
            entry["blocker"] = BLOCK_CAPACITY
            entry["blocker_detail"] = _capacity_detail(data, capacity, hh)
        elif planner.enumerate_paths(hh):
            gaps = []
            for edges in planner.enumerate_paths(hh):
                states = [current] + [e.to for e in edges]
                gaps.extend(planner.path_evidence_gaps(hh, states[1:]))
            entry["status"] = "blocked"
            entry["blocker"] = BLOCK_STAGE_EVIDENCE
            entry["evidence_gaps"] = [g.to_dict() for g in _dedupe(gaps)]
            retreats = planner.retreat_options(hh, capacity)
            if retreats:
                entry["retreat_options"] = [r.to_dict() for r in retreats]
        else:
            entry["status"] = "blocked"
            entry["blocker"] = BLOCK_NO_TRANSITION
        results[hh.id] = entry

    return {
        "now": data.now.isoformat(),
        "households": results,
        "windows": {
            wid: {
                "capacity": w.capacity,
                "remaining": capacity.remaining.get(wid, 0),
                "open": data.window_open(w),
            }
            for wid, w in data.windows.items()
        },
        "retrial_slots": [
            _retrial_view(data, slot) for slot in data.retrial_slots
        ],
    }


def _dedupe(gaps: list[ScopeStatus]) -> list[ScopeStatus]:
    seen: set[tuple] = set()
    out: list[ScopeStatus] = []
    for g in gaps:
        key = (g.function, tuple(sorted((k, repr(v)) for k, v in g.scope.items())))
        if key not in seen:
            seen.add(key)
            out.append(g)
    return out


def _capacity_detail(data: MaintenanceData, capacity: _Capacity, hh: Household) -> dict:
    return {
        "windows": {
            wid: capacity.remaining.get(wid, 0) for wid in hh.windows
        },
        "note": "存在证据可行的路径，但剩余窗口工时或替换件不足",
    }


def _retrial_view(data: MaintenanceData, slot: dict) -> dict:
    """复验条件能解除哪些共同障碍（尚无测量结果）。"""
    unblocks = []
    for function in slot["functions"]:
        status = data.support(function, slot["state"], slot["topology"])
        if not status.ok:
            unblocks.append(status.to_dict())
    return {
        "id": slot["id"],
        "state": slot["state"],
        "topology": slot["topology"],
        "functions": list(slot["functions"]),
        "window": slot["window"],
        "would_restore": unblocks,
    }


# ----------------------------------------------------------------------
def bundle_view(data: MaintenanceData, batch: dict, bundle_id: str) -> dict:
    """客服视图：当时承诺、已执行步骤、现在可兑现的安排、何时转人工。"""
    bundle = next(b for b in data.sold_bundles if b["id"] == bundle_id)
    hh = data.households[bundle["household"]]
    contracted_at = parse_time(bundle["contracted_at"])
    evidence_then = data.evidence_at(contracted_at)

    promised = []
    for state in bundle["planned"]:
        for function in bundle["mandatory"]:
            status = evidence_then.status(function, state=state, topology=hh.topology)
            promised.append({
                "state": state,
                "function": function,
                "status": status.status,
                "complete_installations": status.complete_installations,
            })

    later_corrections = [
        c for c in data.corrections
        if parse_time(c["known_at"]) > contracted_at
        and c["trial"] in set(bundle.get("cited_trials", []))
    ]
    executed = [
        d for d in data.dispatches
        if d["household"] == hh.id and d["status"] == "executed"
    ]
    household_now = batch["households"].get(hh.id, {})
    fulfillable = household_now.get("status") in ("advance", "at_goal")
    view = {
        "bundle": bundle_id,
        "household": hh.id,
        "contracted_at": bundle["contracted_at"],
        "mandatory": list(bundle["mandatory"]),
        "promised_support": promised,
        "corrections_since": [
            {"id": c["id"], "trial": c["trial"], "known_at": c["known_at"],
             "changed_fields": c["changed_fields"]}
            for c in later_corrections
        ],
        "executed_steps": [
            {"id": d["id"], "from": d["from"], "to": d["to"], "window": d["window"]}
            for d in executed
        ],
        "current": household_now,
        "fulfillable_now": fulfillable,
        "manual_support": not fulfillable,
    }
    if not fulfillable:
        view["manual_reason"] = "当前证据或容量下已无可兑现安排，改走人工支持"
    return view
