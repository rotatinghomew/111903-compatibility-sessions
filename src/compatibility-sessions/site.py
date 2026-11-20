"""共享网关站点：按整个共享域判断连续可用。

口径：
- 工单改网关时，仍挂在该网关上的家庭立即进入新组合，无论是否提出升级。
- 备用网关实际接上后，托管家庭持续占用端口，直到回执确认重新接回。
- 已发工单但无回执的连接关系未核实：不按原态或计划终态承诺下一步，
  两端可能用到的端口都不释放。
- 私有部件改动只需该户许可；共享设备变更要符合所有受影响家庭和
  物业共同的维护许可。许可不足时指出受谁限制、哪项承诺被影响。
- 单户实验不能直接支持多户同时接入；证据不跨接入数量、拓扑或
  临时设备继承。
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
)

SCOPE_KEYS = ("topology", "hub", "bell", "sensor", "temporary", "households_connected")

_RANK = {"failed": 0, "unobserved": 1, "supported": 2}


@dataclass
class SiteHome:
    id: str
    doorbell: str
    sensor: str
    topology: str
    priority: int
    goal: dict
    continuous: list[str]


@dataclass
class SiteState:
    """共享域联合状态：网关版本、各户接入位置、各户门铃版本。"""

    versions: tuple[tuple[str, str], ...]      # (gateway_id, version)
    attached: tuple[tuple[str, str], ...]      # (home_id, gateway_id)
    doorbells: tuple[tuple[str, str], ...]     # (home_id, version)

    def gateway_of(self, home: str) -> str:
        return dict(self.attached)[home]

    def version_of(self, gateway: str) -> str:
        return dict(self.versions)[gateway]

    def doorbell_of(self, home: str) -> str:
        return dict(self.doorbells)[home]

    def homes_on(self, gateway: str) -> list[str]:
        return sorted(h for h, g in self.attached if g == gateway)


class SharedSite:
    def __init__(self, site: dict, protocol: dict[str, FunctionProtocol]) -> None:
        self.site = site
        self.protocol = protocol
        self.id = site["site"]
        self.gateways = {g["id"]: dict(g) for g in site["gateways"]}
        self.homes = {
            h["id"]: SiteHome(
                id=h["id"],
                doorbell=h["doorbell"],
                sensor=h["sensor"],
                topology=h["topology"],
                priority=h["priority"],
                goal=dict(h["goal"]),
                continuous=list(h["continuous"]),
            )
            for h in site["homes"]
        }
        self.windows = {w["id"]: dict(w) for w in site["windows"]}
        for w in self.windows.values():
            w["start_t"] = parse_time(w["start"])
            w["end_t"] = parse_time(w["end"])
        self.permissions = list(site["permissions"])
        self.operations = {op["id"]: dict(op) for op in site["operations"]}
        self.work_orders = {wo["id"]: dict(wo) for wo in site["work_orders"]}
        self.receipts = {r["work_order"]: dict(r) for r in site["receipts"]}
        self.inventory_notices = list(site["inventory_notices"])
        self.scope_rechecks = list(site["scope_rechecks"])
        self.sold_bundles = list(site.get("sold_bundles", []))
        self.now = self._derive_now()
        stage_keys = tuple(sorted({s for p in protocol.values() for s in p.stages}))
        self._stage_keys = stage_keys
        self._raw_tests = list(site["tests"])
        self.evidence = self._build_evidence(self._raw_tests)

    @classmethod
    def load(cls, root: str | Path) -> "SharedSite":
        root = Path(root)
        site = json.loads((root / "shared_gateway.json").read_text(encoding="utf-8"))
        service = json.loads((root / "maintenance_windows.json").read_text(encoding="utf-8"))
        protocol = {
            name: FunctionProtocol.from_record(rec)
            for name, rec in service["protocol"].items()
        }
        return cls(site, protocol)

    def _build_evidence(self, tests: list[dict]) -> EvidenceBase:
        trials = [normalize_trial(t, SCOPE_KEYS, self._stage_keys) for t in tests]
        return EvidenceBase(self.protocol, trials, [], SCOPE_KEYS)

    def _derive_now(self) -> datetime:
        stamps: list[datetime] = []
        for r in self.site["receipts"]:
            stamps.append(parse_time(r["known_at"]))
        for n in self.site["inventory_notices"]:
            stamps.append(parse_time(n["known_at"]))
        for wo in self.site["work_orders"]:
            stamps.append(parse_time(wo["started"]))
        return max(stamps)

    # ------------------------------------------------------------------
    def resolve_connections(self) -> dict:
        """按接线清单与已确认回执还原真实连接关系。"""
        attached: dict[str, str] = {}
        source: dict[str, str] = {}
        for row in self.site["initial_connections"]:
            attached[row["home"]] = row["gateway"]
            source[row["home"]] = "initial_connections"
        notices = sorted(
            (n for n in self.inventory_notices if parse_time(n["effective_at"]) <= self.now),
            key=lambda n: parse_time(n["effective_at"]),
        )
        for n in notices:
            attached[n["home"]] = n["gateway"]
            source[n["home"]] = n["id"]
        pending: dict[str, dict] = {}
        confirmed: dict[str, str] = {}
        for wo_id, wo in sorted(
            self.work_orders.items(), key=lambda kv: parse_time(kv[1]["started"])
        ):
            op = self.operations[wo["operation"]]
            if op["kind"] != "move_home":
                continue
            receipt = self.receipts.get(wo_id)
            if receipt is None:
                pending[wo["home"]] = {"work_order": wo_id, "from": op["from"], "to": op["to"]}
                continue
            attached[wo["home"]] = receipt["gateway"]
            source[wo["home"]] = receipt["id"]
            confirmed[wo["home"]] = receipt["id"]
        result = {}
        for home in self.homes:
            if home in pending:
                result[home] = {
                    "gateway": attached.get(home),
                    "status": "pending",
                    "pending": pending[home],
                }
            else:
                result[home] = {
                    "gateway": attached.get(home),
                    "status": "confirmed",
                    "source": source.get(home),
                }
        return result

    def current_state(self) -> SiteState:
        connections = self.resolve_connections()
        return SiteState(
            versions=tuple(sorted((g, gw["version"]) for g, gw in self.gateways.items())),
            attached=tuple(sorted((h, c["gateway"]) for h, c in connections.items())),
            doorbells=tuple(sorted((h, home.doorbell) for h, home in self.homes.items())),
        )

    def pending_orders(self) -> list[dict]:
        out = []
        for wo_id, wo in self.work_orders.items():
            if wo_id not in self.receipts:
                out.append({"id": wo_id, **wo})
        return out

    # ------------------------------------------------------------------
    def port_usage(self, state: SiteState) -> dict[str, dict]:
        usage = {
            g: {"used": 0, "held": 0, "limit": gw["port_limit"]}
            for g, gw in self.gateways.items()
        }
        for home, gateway in state.attached:
            usage[gateway]["used"] += 1
        # 无回执的工单：两端端口都保留
        for wo in self.pending_orders():
            op = self.operations[wo["operation"]]
            if op["kind"] == "move_home":
                for g in (op["from"], op["to"]):
                    usage[g]["held"] += 1
        return usage

    def home_scope(self, state: SiteState, home_id: str) -> dict:
        home = self.homes[home_id]
        gateway = state.gateway_of(home_id)
        gw = self.gateways[gateway]
        return {
            "topology": home.topology,
            "hub": state.version_of(gateway),
            "bell": state.doorbell_of(home_id),
            "sensor": home.sensor,
            "temporary": not gw["fixed"],
            "households_connected": len(state.homes_on(gateway)),
        }

    def commitment_status(
        self, state: SiteState, home_id: str, evidence: EvidenceBase | None = None
    ) -> dict[str, ScopeStatus]:
        ev = evidence or self.evidence
        home = self.homes[home_id]
        scope = self.home_scope(state, home_id)
        return {f: ev.status(f, **scope) for f in home.continuous}

    # ------------------------------------------------------------------
    def permits(
        self, party: str, kind: str, window_id: str, device: str | None = None
    ) -> bool:
        for row in self.permissions:
            if row["party"] != party:
                continue
            if device is not None and row.get("device") not in (None, device):
                continue
            if window_id in row["windows"] and kind in row["operations"]:
                return True
        return False

    def _permission_gaps(
        self, state: SiteState, op: dict, home_id: str | None, window_id: str
    ) -> list[dict]:
        kind = op["kind"]
        gaps: list[dict] = []
        if kind == "upgrade_gateway":
            gateway = op["gateway"]
            if not self.permits("property-4", kind, window_id, device=gateway):
                gaps.append({"party": "property-4", "kind": kind, "window": window_id})
            for hid in state.homes_on(gateway):
                if not self.permits(hid, kind, window_id):
                    gaps.append({"party": hid, "kind": kind, "window": window_id})
        elif kind == "move_home":
            if not self.permits(home_id, kind, window_id):
                gaps.append({"party": home_id, "kind": kind, "window": window_id})
            if not (
                self.permits("property-4", kind, window_id, device=op["from"])
                or self.permits("property-4", kind, window_id, device=op["to"])
            ):
                gaps.append({"party": "property-4", "kind": kind, "window": window_id})
        elif kind == "swap_doorbell":
            if not self.permits(home_id, kind, window_id):
                gaps.append({"party": home_id, "kind": kind, "window": window_id})
        return gaps

    # ------------------------------------------------------------------
    def apply_operation(self, state: SiteState, op: dict, home_id: str | None) -> SiteState:
        kind = op["kind"]
        versions = dict(state.versions)
        attached = dict(state.attached)
        doorbells = dict(state.doorbells)
        if kind == "upgrade_gateway":
            versions[op["gateway"]] = op["to"]
        elif kind == "move_home":
            attached[home_id] = op["to"]
        elif kind == "swap_doorbell":
            doorbells[home_id] = op["to"]
        return SiteState(
            versions=tuple(sorted(versions.items())),
            attached=tuple(sorted(attached.items())),
            doorbells=tuple(sorted(doorbells.items())),
        )

    def affected_homes(self, state: SiteState, op: dict, home_id: str | None) -> set[str]:
        kind = op["kind"]
        if kind == "upgrade_gateway":
            return set(state.homes_on(op["gateway"]))
        if kind == "move_home":
            return (
                set(state.homes_on(op["from"]))
                | set(state.homes_on(op["to"]))
                | {home_id}
            )
        return {home_id}

    def op_subjects(
        self, state: SiteState, op: dict, home_id: str | None
    ) -> tuple[set[str], set[str]]:
        """（直接对象, 旁观家庭）。

        直接对象的新组合必须证据足够；旁观家庭只是接入数被动变化，
        不更差时放行但标注警告。
        """
        kind = op["kind"]
        affected = self.affected_homes(state, op, home_id)
        if kind == "upgrade_gateway":
            # 网关升级时，挂在上面的每户都是直接对象
            return set(affected), set()
        if kind == "move_home":
            return {home_id}, affected - {home_id}
        return {home_id}, set()

    def evaluate_operation(
        self,
        state: SiteState,
        op_id: str,
        home_id: str | None,
        window_id: str,
        evidence: EvidenceBase | None = None,
        extra_permissions: list[dict] | None = None,
    ) -> dict:
        """评估一个候选操作：许可、证据、端口、窗口工时。"""
        ev = evidence or self.evidence
        op = self.operations[op_id]
        saved_permissions = self.permissions
        if extra_permissions:
            self.permissions = saved_permissions + list(extra_permissions)
        try:
            permission_gaps = self._permission_gaps(state, op, home_id, window_id)
        finally:
            self.permissions = saved_permissions

        blockers: list[dict] = []
        if permission_gaps:
            blockers.append({"type": "permission", "gaps": permission_gaps})

        # 连接关系未核实的家庭：不按原态或计划终态承诺依赖它的下一步
        connections = self.resolve_connections()
        affected = self.affected_homes(state, op, home_id)
        unverified = sorted(
            hid for hid in affected
            if connections.get(hid, {}).get("status") == "pending"
        )
        if unverified:
            blockers.append({
                "type": "unverified",
                "homes": unverified,
                "reason": "工单已发未回执，实际连接关系未核对",
            })

        window = self.windows[window_id]
        if window["end_t"] <= self.now:
            blockers.append({"type": "capacity", "reason": "window_closed"})

        new_state = self.apply_operation(state, op, home_id)
        # 端口：move_home 目标网关需要空位
        if op["kind"] == "move_home":
            usage = self.port_usage(state)
            target = op["to"]
            free = usage[target]["limit"] - usage[target]["used"] - usage[target]["held"]
            if free < 1:
                blockers.append(
                    {"type": "capacity", "reason": "no_free_port", "gateway": target}
                )
        # 证据：直接对象的新组合必须支持持续承诺；旁观家庭不得更差
        subjects, bystanders = self.op_subjects(state, op, home_id)
        evidence_gaps: list[dict] = []
        warnings: list[dict] = []
        for hid in sorted(subjects | bystanders):
            home = self.homes[hid]
            before = self.commitment_status(state, hid, ev)
            after = self.commitment_status(new_state, hid, ev)
            for function in home.continuous:
                old, new = before[function], after[function]
                if hid in subjects:
                    if not new.ok:
                        evidence_gaps.append({
                            "home": hid,
                            "function": function,
                            "status": new.status,
                            "scope": new.scope,
                            "complete_installations": new.complete_installations,
                            "required_installations": new.required_installations,
                        })
                elif _RANK[new.status] < _RANK[old.status]:
                    evidence_gaps.append({
                        "home": hid,
                        "function": function,
                        "status": new.status,
                        "scope": new.scope,
                        "note": "旁观家庭证据被拉低",
                    })
                elif not new.ok and not old.ok:
                    warnings.append({
                        "home": hid,
                        "function": function,
                        "status": new.status,
                        "scope": new.scope,
                        "note": "旁观家庭现状已不足，接入数变化后仍未补足",
                    })
        if evidence_gaps:
            blockers.append({"type": "evidence", "gaps": evidence_gaps})

        return {
            "operation": op_id,
            "kind": op["kind"],
            "home": home_id,
            "window": window_id,
            "minutes": op["minutes"],
            "executable": not blockers,
            "blockers": blockers,
            "warnings": warnings,
            "affected": sorted(self.affected_homes(state, op, home_id)),
        }

    # ------------------------------------------------------------------
    def candidate_operations(self, state: SiteState) -> list[tuple[str, str | None]]:
        """（操作, 目标家庭）候选清单。"""
        out: list[tuple[str, str | None]] = []
        for op_id, op in self.operations.items():
            if op["kind"] == "upgrade_gateway":
                out.append((op_id, None))
            elif op["kind"] == "move_home":
                for hid in self.homes:
                    if state.gateway_of(hid) == op["from"]:
                        out.append((op_id, hid))
            elif op["kind"] == "swap_doorbell":
                for hid, home in self.homes.items():
                    if state.doorbell_of(hid) == op["from"]:
                        out.append((op_id, hid))
        return sorted(out)

    def open_windows(self) -> list[str]:
        return sorted(
            (wid for wid, w in self.windows.items() if w["end_t"] > self.now),
            key=lambda wid: self.windows[wid]["start_t"],
        )

    def at_goal(self, state: SiteState, home_id: str) -> bool:
        home = self.homes[home_id]
        gateway = state.gateway_of(home_id)
        return (
            state.version_of(gateway) == home.goal.get("hub")
            and state.doorbell_of(home_id) == home.goal.get("doorbell")
        )

    # ------------------------------------------------------------------
    def analyze(
        self,
        extra_tests: list[dict] | None = None,
        extra_permissions: list[dict] | None = None,
    ) -> dict:
        state = self.current_state()
        evidence = (
            self._build_evidence(self._raw_tests + list(extra_tests))
            if extra_tests
            else self.evidence
        )
        connections = self.resolve_connections()
        ports = self.port_usage(state)

        homes_view: dict[str, dict] = {}
        for hid, home in sorted(self.homes.items()):
            commitments = self.commitment_status(state, hid, evidence)
            homes_view[hid] = {
                "gateway": connections[hid]["gateway"],
                "connection": connections[hid]["status"],
                "topology": home.topology,
                "at_goal": self.at_goal(state, hid),
                "goal": home.goal,
                "commitments": {f: s.to_dict() for f, s in commitments.items()},
                "commitment_gap": any(not s.ok for s in commitments.values()),
            }

        operations_view = []
        for op_id, hid in self.candidate_operations(state):
            for wid in self.open_windows():
                operations_view.append(
                    self.evaluate_operation(
                        state, op_id, hid, wid, evidence, extra_permissions
                    )
                )

        upgradeable = sorted(
            hid for hid in self.homes if homes_view[hid]["at_goal"] is False
            and any(
                v["executable"]
                and (v["home"] == hid or (v["kind"] == "upgrade_gateway" and hid in v["affected"]))
                for v in operations_view
            )
        )
        mid_transition = sorted(
            hid for hid in self.homes
            if not homes_view[hid]["at_goal"]
            and not homes_view[hid]["commitment_gap"]
            and self.gateways[connections[hid]["gateway"]]["fixed"] is False
        )
        gaps = sorted(hid for hid in self.homes if homes_view[hid]["commitment_gap"])

        return {
            "site": self.id,
            "now": self.now.isoformat(),
            "connections": connections,
            "pending_orders": self.pending_orders(),
            "gateways": {
                g: {
                    "version": self.gateways[g]["version"],
                    "fixed": self.gateways[g]["fixed"],
                    **ports[g],
                }
                for g in sorted(self.gateways)
            },
            "homes": homes_view,
            "batches": {
                "upgradeable": upgradeable,
                "mid_transition": mid_transition,
                "commitment_gaps": gaps,
            },
            "candidate_operations": operations_view,
        }

    # ------------------------------------------------------------------
    def recheck_report(self) -> list[dict]:
        """每个复验条件：解除哪些障碍、补上后仍受什么限制。"""
        report = []
        base = self.analyze()
        base_exec = {
            (v["operation"], v["home"], v["window"])
            for v in base["candidate_operations"]
            if v["executable"]
        }

        def gap_keys(verdict: dict) -> set[tuple]:
            keys = set()
            for b in verdict["blockers"]:
                if b["type"] != "evidence":
                    continue
                for gap in b["gaps"]:
                    keys.add(
                        (
                            gap["function"],
                            tuple(sorted((k, repr(v)) for k, v in gap["scope"].items())),
                        )
                    )
            return keys

        base_verdicts = {
            (v["operation"], v["home"], v["window"]): v
            for v in base["candidate_operations"]
        }
        for slot in self.scope_rechecks:
            slot_keys = {
                (
                    function,
                    tuple(sorted((k, repr(slot[k])) for k in SCOPE_KEYS)),
                )
                for function in slot["functions"]
            }
            synthetic = []
            for function in slot["functions"]:
                proto = self.protocol[function]
                for i in range(proto.min_complete_installations):
                    rec = {
                        "id": f"{slot['id']}-sim-{function}-{i}",
                        "installation": f"{slot['id']}-sim-{function}-{i}",
                        "function": function,
                        **{k: slot[k] for k in SCOPE_KEYS},
                        **{s: "pass" for s in proto.stages},
                    }
                    synthetic.append(rec)
            sim = self.analyze(extra_tests=synthetic)
            sim_verdicts = {
                (v["operation"], v["home"], v["window"]): v
                for v in sim["candidate_operations"]
            }
            sim_exec = {k for k, v in sim_verdicts.items() if v["executable"]}
            newly = sorted(sim_exec - base_exec)
            # 与本复验相关、但完成后仍被其他阶段/许可/容量挡住的操作
            still = []
            for key, base_v in base_verdicts.items():
                if key in sim_exec or not (gap_keys(base_v) & slot_keys):
                    continue
                sim_v = sim_verdicts[key]
                still.append({
                    "operation": key[0],
                    "home": key[1],
                    "window": key[2],
                    "blockers": sorted({b["type"] for b in sim_v["blockers"]}),
                })
            window_open = self.windows[slot["window"]]["end_t"] > self.now
            report.append({
                "id": slot["id"],
                "window": slot["window"],
                "window_open": window_open,
                "scope": {k: slot[k] for k in SCOPE_KEYS},
                "functions": list(slot["functions"]),
                "newly_executable": [
                    {"operation": op, "home": home, "window": wid}
                    for op, home, wid in newly
                ],
                "still_blocked": still,
            })
        return report

    def permission_what_if(self) -> list[dict]:
        """补齐某项缺失许可后，哪些路径会改变。"""
        base = self.analyze()
        missing: set[tuple[str, str, str]] = set()
        for v in base["candidate_operations"]:
            for b in v["blockers"]:
                if b["type"] == "permission":
                    for gap in b["gaps"]:
                        missing.add((gap["party"], gap["kind"], gap["window"]))
        out = []
        for party, kind, window in sorted(missing):
            grant = {"party": party, "windows": [window], "operations": [kind]}
            sim = self.analyze(extra_permissions=[grant])
            base_exec = {
                (v["operation"], v["home"], v["window"])
                for v in base["candidate_operations"] if v["executable"]
            }
            sim_exec = {
                (v["operation"], v["home"], v["window"])
                for v in sim["candidate_operations"] if v["executable"]
            }
            out.append({
                "grant": {"party": party, "kind": kind, "window": window},
                "newly_executable": sorted(sim_exec - base_exec),
                "still_blocked_count": sum(
                    1 for v in sim["candidate_operations"] if not v["executable"]
                ),
            })
        return out

    def bundle_view(self, report: dict, bundle_id: str) -> dict:
        """站点已售套装的客服视图。"""
        bundle = next(b for b in self.sold_bundles if b["id"] == bundle_id)
        home_id = bundle["home"]
        home_report = report["homes"][home_id]
        cited = []
        for trial_id in bundle.get("cited_trials", []):
            raw = next((t for t in self._raw_tests if t["id"] == trial_id), None)
            if raw is None:
                continue
            scope = {k: raw.get(k) for k in SCOPE_KEYS}
            status = self.evidence.status(raw["function"], **scope)
            cited.append({
                "trial": trial_id,
                "function": raw["function"],
                "scope": scope,
                "status": status.status,
            })
        gap = home_report["commitment_gap"]
        return {
            "bundle": bundle_id,
            "home": home_id,
            "sold_at": bundle["sold_at"],
            "continuous": list(bundle["continuous"]),
            "cited_trials": cited,
            "current": home_report,
            "fulfillable_now": not gap,
            "manual_support": gap,
            "manual_reason": "当前组合的持续承诺证据不足，改走人工支持" if gap else None,
        }


def joint_recheck_coverage(sites: list[SharedSite]) -> list[dict]:
    """同规格复验在多个共享站点的联合覆盖。

    先判断各站点复验窗口是否仍开放（许可与机位前提），再汇总该规格
    缺口在哪些站点真的能被消除；不因单户数量多就放大可放行的服务数。
    """
    groups: dict[tuple, dict] = {}
    for site in sites:
        effects = {r["id"]: r for r in site.recheck_report()}
        for slot in site.scope_rechecks:
            signature = (
                tuple(sorted((k, repr(slot[k])) for k in SCOPE_KEYS)),
                tuple(sorted(slot["functions"])),
            )
            group = groups.setdefault(
                signature,
                {
                    "scope": {k: slot[k] for k in SCOPE_KEYS},
                    "functions": tuple(sorted(slot["functions"])),
                    "sites": [],
                },
            )
            effect = effects.get(slot["id"], {})
            group["sites"].append({
                "site": site.id,
                "recheck": slot["id"],
                "window": slot["window"],
                "window_open": site.windows[slot["window"]]["end_t"] > site.now,
                "newly_executable": effect.get("newly_executable", []),
                "still_blocked": effect.get("still_blocked", []),
            })
    return list(groups.values())
