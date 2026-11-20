"""把分析结果渲染为中文文本报告。"""
from __future__ import annotations

_STATUS_ZH = {
    "supported": "证据足够",
    "unobserved": "未观察到完整阶段",
    "failed": "确认失败",
}

_BLOCKER_ZH = {
    "stage_evidence_missing": "某个中间阶段缺证",
    "no_feasible_transition": "当前已无可行转换",
    "capacity_insufficient": "有路径但伙伴容量不足",
}


def _scope_text(scope: dict) -> str:
    return ",".join(f"{k}={v}" for k, v in scope.items())


def render_households(batch: dict) -> str:
    lines = [f"数据时点 {batch['now']}", "", "== 家庭批次（维护窗口） =="]
    for hid, h in batch["households"].items():
        lines.append(
            f"[{hid}] 优先级{h['priority']} 拓扑={h['topology']} "
            f"现状={h['current_state']} 目标={h['goal']}"
        )
        for function, support in h["current_support"].items():
            lines.append(
                f"  当前承诺 {function}: {_STATUS_ZH[support['status']]}"
                f"（完整安装 {support['complete_installations']}/{support['required_installations']}）"
            )
        if h["at_risk"]:
            lines.append("  ※ 当前所处组合的持续承诺证据不足，优先处理")
        if h["status"] == "at_goal":
            lines.append("  结论：已达目标组合")
        elif h["status"] == "advance":
            plan = h["plan"]
            chain = plan["states"][0]
            for s in plan["steps"]:
                chain += f" —{s['minutes']}′@{s['window']}→ {s['to']}"
            lines.append(f"  建议：{chain}")
            for release in plan.get("releases", []):
                lines.append(f"  显式释放预留 {release}（工时与替换件回到公用池）")
            for hold in plan.get("equipment_holds", []):
                lines.append(f"  替换件 {hold} 随户保留至离开临时状态")
        elif h["status"] == "blocked":
            lines.append(f"  结论：保留原组合（{_BLOCKER_ZH[h['blocker']]}）")
            for gap in h.get("evidence_gaps", []):
                lines.append(
                    f"    缺证 {gap['function']} @ {_scope_text(gap['scope'])}: "
                    f"{_STATUS_ZH[gap['status']]}"
                    f"（完整安装 {gap['complete_installations']}/{gap['required_installations']}）"
                )
        for retreat in h.get("retreat_options", []):
            step = retreat["steps"][0]
            lines.append(
                f"  备选安全退回：{step['from']}→{step['to']}"
                f"（{step['minutes']}′@{step['window']}）"
            )
    lines.append("")
    lines.append("窗口剩余工时：")
    for wid, w in batch["windows"].items():
        state = "开放" if w["open"] else "已关闭"
        lines.append(f"  {wid}: 剩余 {w['remaining']}′/{w['capacity']}′（{state}）")
    if batch.get("retrial_slots"):
        lines.append("")
        lines.append("复验条件（尚无测量结果）：")
        for slot in batch["retrial_slots"]:
            restores = "、".join(
                f"{r['function']}@{r['scope'].get('state')}" for r in slot["would_restore"]
            )
            lines.append(
                f"  {slot['id']}（{slot['window']}）：完成后可解除 {restores or '无'} 的共同障碍"
            )
    return "\n".join(lines)


def render_site(report: dict, rechecks: list[dict], what_ifs: list[dict], joint: list[dict] | None = None) -> str:
    lines = [f"== 共享站点 {report['site']} ==", f"数据时点 {report['now']}", ""]
    lines.append("连接现状（回执/盘点确认）：")
    for hid, conn in sorted(report["connections"].items()):
        if conn["status"] == "pending":
            p = conn["pending"]
            lines.append(
                f"  {hid}: 工单 {p['work_order']} 已发未回执，"
                f"{p['from']}→{p['to']} 未核实，两端端口保留"
            )
        else:
            lines.append(f"  {hid}: 挂在 {conn['gateway']}（{conn.get('source') or '初始接线'}）")
    lines.append("")
    lines.append("网关与端口：")
    for gid, g in report["gateways"].items():
        kind = "固定" if g["fixed"] else "备用"
        lines.append(
            f"  {gid}（{kind}，版本 {g['version']}）："
            f"占用 {g['used']}/{g['limit']} 端口"
            + (f"，另有 {g['held']} 个端口被未回执工单保留" if g["held"] else "")
        )
    lines.append("")
    lines.append("各户当前承诺：")
    for hid, h in sorted(report["homes"].items()):
        marks = []
        if h["at_goal"]:
            marks.append("已达目标")
        if h["commitment_gap"]:
            marks.append("承诺缺口")
        suffix = "　" + "、".join(marks) if marks else ""
        for function, support in h["commitments"].items():
            lines.append(
                f"  {hid}（{h['gateway']}）{function}: {_STATUS_ZH[support['status']]}"
                f"（完整安装 {support['complete_installations']}/{support['required_installations']}）"
                + suffix
            )
            suffix = ""
    lines.append("")
    batches = report["batches"]
    lines.append(
        f"批次结论：可升级 {batches['upgradeable'] or '无'}；"
        f"停在中间态 {batches['mid_transition'] or '无'}；"
        f"承诺缺口 {batches['commitment_gaps'] or '无'}"
    )
    blocked = [v for v in report["candidate_operations"] if not v["executable"]]
    if blocked:
        lines.append("")
        lines.append("候选操作的阻塞原因：")
        for v in blocked:
            who = v["home"] or "（共享）"
            reasons = []
            for b in v["blockers"]:
                if b["type"] == "permission":
                    parties = "、".join(g["party"] for g in b["gaps"])
                    reasons.append(f"许可不足（受 {parties} 限制）")
                elif b["type"] == "evidence":
                    gaps = "、".join(
                        f"{g['home']} 的 {g['function']}（{_STATUS_ZH[g['status']]}）"
                        for g in b["gaps"]
                    )
                    reasons.append(f"证据不足：{gaps}")
                elif b["type"] == "capacity":
                    reasons.append(f"容量不足（{b.get('reason')}）")
            lines.append(f"  {v['operation']} {who} @{v['window']}: " + "；".join(reasons))
        warned = [v for v in report["candidate_operations"] if v["warnings"]]
        for v in warned:
            for w in v["warnings"]:
                lines.append(
                    f"  ※ {v['operation']} {v['home'] or ''}@{v['window']} 旁观提示："
                    f"{w['home']} 的 {w['function']} {w['note']}"
                )
    if rechecks:
        lines.append("")
        lines.append("复验条件的影响：")
        for r in rechecks:
            state = "窗口开放" if r["window_open"] else "窗口已关闭"
            newly = "、".join(
                f"{n['operation']}({n['home'] or '共享'}@{n['window']})"
                for n in r["newly_executable"]
            )
            lines.append(f"  {r['id']}（{r['window']}，{state}）：完成后放行 {newly or '无'}")
            for s in r["still_blocked"]:
                lines.append(
                    f"    仍受阻：{s['operation']}({s['home'] or '共享'}@{s['window']}) "
                    f"← {'、'.join(s['blockers'])}"
                )
    if what_ifs:
        lines.append("")
        lines.append("若补齐许可：")
        for w in what_ifs:
            g = w["grant"]
            newly = "、".join(
                f"{op}({home or '共享'}@{wid})" for op, home, wid in w["newly_executable"]
            )
            lines.append(
                f"  {g['party']} 授予 {g['kind']}@{g['window']}：新增可执行 {newly or '无'}"
                f"（其余 {w['still_blocked_count']} 项仍受证据或容量限制）"
            )
    if joint:
        lines.append("")
        lines.append("同规格复验的联合覆盖（先核对窗口与机位前提）：")
        for group in joint:
            lines.append(
                f"  规格 {_scope_text(group['scope'])}（{'、'.join(group['functions'])}）："
            )
            for s in group["sites"]:
                ready = "窗口开放" if s["window_open"] else "窗口已关闭，暂不具备条件"
                newly = "、".join(
                    f"{n['operation']}({n['home'] or '共享'}@{n['window']})"
                    for n in s["newly_executable"]
                )
                lines.append(
                    f"    {s['site']} {s['recheck']}@{s['window']}：{ready}；"
                    f"完成后放行 {newly or '无'}"
                )
    return "\n".join(lines)


def render_site_bundle(view: dict) -> str:
    lines = [
        f"== 已售套装 {view['bundle']}（{view['home']}，共享站点） ==",
        f"售出于 {view['sold_at']}，必须持续可用：{'、'.join(view['continuous'])}",
    ]
    if view["cited_trials"]:
        lines.append("签约时引用的试验：")
        for c in view["cited_trials"]:
            lines.append(
                f"  {c['trial']}（{c['function']} @ {_scope_text(c['scope'])}）: "
                f"{_STATUS_ZH[c['status']]}"
            )
    else:
        lines.append("签约时未引用任何试验记录。")
    current = view["current"]
    lines.append(f"当前挂在 {current['gateway']}（{current['connection']}）：")
    for function, support in current["commitments"].items():
        lines.append(
            f"  {function}: {_STATUS_ZH[support['status']]}"
            f"（完整安装 {support['complete_installations']}/{support['required_installations']}）"
        )
    if view["fulfillable_now"]:
        lines.append("现在的安置仍可兑现承诺；升级批次见站点视图。")
    else:
        lines.append(f"现在无法兑现：{view['manual_reason']}")
    return "\n".join(lines)


def render_bundle(view: dict) -> str:
    lines = [
        f"== 已售套装 {view['bundle']}（{view['household']}） ==",
        f"签约于 {view['contracted_at']}，必须持续可用：{'、'.join(view['mandatory'])}",
        "当时承诺的支持：",
    ]
    for p in view["promised_support"]:
        lines.append(
            f"  {p['state']} 的 {p['function']}: {_STATUS_ZH[p['status']]}"
            f"（完整安装 {p['complete_installations']}）"
        )
    if view["corrections_since"]:
        lines.append("签约后的证据更正：")
        for c in view["corrections_since"]:
            lines.append(
                f"  {c['id']}：{c['trial']} 的 {c['changed_fields']}（{c['known_at']} 知悉）"
            )
    if view["executed_steps"]:
        lines.append("已执行步骤：")
        for s in view["executed_steps"]:
            lines.append(f"  {s['id']}: {s['from']}→{s['to']}（{s['window']}）")
    current = view.get("current") or {}
    if current:
        lines.append(f"现状：{current.get('current_state')}（结论见家庭批次）")
    if view["fulfillable_now"]:
        lines.append("现在仍可兑现承诺：按建议路径继续即可。")
    else:
        lines.append(f"现在无法兑现：{view.get('manual_reason', '改走人工支持')}")
    return "\n".join(lines)
