"""证据范围与支持状态。

规则要点：
- 试验按（配套范围 × 功能）归组；同一安装反复登记只算一次独立观测。
- 空阶段为未观察；任一必需阶段登记为 fail 即确认失败。
- 协议给出每项功能的必需阶段与独立完整安装门槛。
- 更正不重写原始交付记录，只在分析视图里把支持转到新的范围，
  并在新旧两个范围上留下出处说明。
- 没有可转用声明时，证据不跨拓扑、配套、临时状态或接入数量继承。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

PASS = "pass"
FAIL = "fail"

SUPPORTED = "supported"      # 有足够支持
UNOBSERVED = "unobserved"    # 未观察到完整阶段 / 完整安装数不足
FAILED = "failed"            # 确认失败


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


@dataclass(frozen=True)
class FunctionProtocol:
    stages: tuple[str, ...]
    min_complete_installations: int

    @classmethod
    def from_record(cls, record: dict) -> "FunctionProtocol":
        return cls(
            stages=tuple(record["stages"]),
            min_complete_installations=int(record["min_complete_installations"]),
        )


@dataclass
class TrialView:
    """一条试验登记的分析视图（已套用更正，原始记录不改写）。"""

    id: str
    installation: str
    function: str
    scope: dict
    stages: dict[str, str | None]
    corrected_by: str | None = None
    original_scope: dict | None = None


@dataclass
class ScopeStatus:
    """一个（范围 × 功能）的支持状态。"""

    function: str
    scope: dict
    status: str
    required_installations: int
    observed_installations: int
    complete_installations: int
    failed_installations: int
    stage_rollup: dict[str, str]
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == SUPPORTED

    def to_dict(self) -> dict:
        return {
            "function": self.function,
            "scope": self.scope,
            "status": self.status,
            "required_installations": self.required_installations,
            "observed_installations": self.observed_installations,
            "complete_installations": self.complete_installations,
            "failed_installations": self.failed_installations,
            "stages": dict(self.stage_rollup),
            "notes": list(self.notes),
        }


class EvidenceBase:
    """按范围归组的试验证据库。

    trials: 规范化记录，键为 id / installation / function / scope / stages。
    corrections: 原始更正登记（id / trial / known_at / changed_fields）。
    scope_keys: 参与范围判定的字段名（如 state、topology、households_connected）。
    """

    def __init__(
        self,
        protocol: dict[str, FunctionProtocol],
        trials: list[dict],
        corrections: list[dict] | None = None,
        scope_keys: tuple[str, ...] = (),
        at: datetime | None = None,
    ) -> None:
        self.protocol = dict(protocol)
        self.scope_keys = tuple(scope_keys)
        self._raw_trials = [dict(t) for t in trials]
        self._corrections = [dict(c) for c in (corrections or [])]
        self._at = at
        self._views = self._build_views()
        # 原范围 -> 被更正转出的试验，用于出处说明
        self._corrected_away: dict[tuple, list[TrialView]] = {}
        for view in self._views:
            if view.corrected_by and view.original_scope is not None:
                key = self._key(view.function, view.original_scope)
                self._corrected_away.setdefault(key, []).append(view)

    # ------------------------------------------------------------------
    def _build_views(self) -> list[TrialView]:
        by_id = {t["id"]: t for t in self._raw_trials}
        changed: dict[str, list[dict]] = {}
        for corr in self._corrections:
            if self._at is not None and parse_time(corr["known_at"]) > self._at:
                continue  # 该时点尚未知悉的更正不生效
            changed.setdefault(corr["trial"], []).append(corr)

        views: list[TrialView] = []
        for trial_id, raw in by_id.items():
            record = {
                "id": raw["id"],
                "installation": raw["installation"],
                "function": raw["function"],
                "scope": {k: raw["scope"].get(k) for k in self.scope_keys},
                "stages": dict(raw["stages"]),
            }
            original_scope = None
            corrected_by = None
            for corr in changed.get(trial_id, []):
                for field_name, value in corr["changed_fields"].items():
                    if field_name in record["stages"]:
                        record["stages"][field_name] = value
                    elif field_name == "function":
                        record["function"] = value
                    elif field_name in self.scope_keys or field_name in raw.get("scope", {}):
                        if original_scope is None:
                            original_scope = dict(record["scope"])
                        record["scope"][field_name] = value
                    else:
                        # 未识别的字段也保留到范围里，避免静默丢弃
                        if original_scope is None:
                            original_scope = dict(record["scope"])
                        record["scope"][field_name] = value
                    corrected_by = corr["id"]
            views.append(
                TrialView(
                    id=record["id"],
                    installation=record["installation"],
                    function=record["function"],
                    scope=record["scope"],
                    stages=record["stages"],
                    corrected_by=corrected_by,
                    original_scope=original_scope,
                )
            )
        return views

    def _key(self, function: str, scope: dict) -> tuple:
        return (function, tuple(sorted((k, repr(v)) for k, v in scope.items())))

    # ------------------------------------------------------------------
    def status(self, function: str, **scope) -> ScopeStatus:
        proto = self.protocol[function]
        wanted = {k: scope.get(k) for k in self.scope_keys}
        key = self._key(function, wanted)

        # 同一安装同一范围内可能有多条登记（反复绑定），先按安装合并。
        installs: dict[str, dict[str, list[str]]] = {}
        notes: list[str] = []
        for view in self._views:
            if view.function != function or self._key(function, view.scope) != key:
                continue
            entry = installs.setdefault(view.installation, {s: [] for s in proto.stages})
            for stage in proto.stages:
                result = view.stages.get(stage)
                if result is not None:
                    entry[stage].append(result)
            if view.corrected_by:
                notes.append(f"{view.id} 经 {view.corrected_by} 更正后转入本范围")
        for view in self._corrected_away.get(key, []):
            notes.append(f"{view.id} 经 {view.corrected_by} 更正后转出原范围")

        complete = 0
        failed = 0
        stage_rollup = {s: "unobserved" for s in proto.stages}
        for stages in installs.values():
            install_failed = any(FAIL in results for results in stages.values())
            if install_failed:
                failed += 1
            if all(PASS in stages[s] and not install_failed for s in proto.stages):
                complete += 1
            for stage in proto.stages:
                results = stages[stage]
                if FAIL in results:
                    stage_rollup[stage] = FAIL
                elif PASS in results and stage_rollup[stage] != FAIL:
                    stage_rollup[stage] = PASS

        if failed:
            status = FAILED
        elif complete >= proto.min_complete_installations:
            status = SUPPORTED
        else:
            status = UNOBSERVED
        return ScopeStatus(
            function=function,
            scope=wanted,
            status=status,
            required_installations=proto.min_complete_installations,
            observed_installations=len(installs),
            complete_installations=complete,
            failed_installations=failed,
            stage_rollup=stage_rollup,
            notes=tuple(sorted(notes)),
        )

    def functions(self) -> list[str]:
        return sorted(self.protocol)


def normalize_trial(raw: dict, scope_keys: tuple[str, ...], stage_keys: tuple[str, ...]) -> dict:
    """把不同来源的试验登记规范化为 EvidenceBase 需要的形状。"""
    return {
        "id": raw["id"],
        "installation": raw["installation"],
        "function": raw["function"],
        "scope": {k: raw.get(k) for k in scope_keys},
        "stages": {s: raw.get(s) for s in stage_keys},
    }
