"""命令行入口：放量建议、站点视图、客服视图。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from . import maintenance, report, site as site_mod


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--data",
        default="data",
        help="资料目录（默认 data/）",
    )
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="compatibility-sessions",
        description="智能家居组合兼容证据与放量建议（本地分析，不控制真实设备）",
    )
    parser.add_argument("--version", action="store_true", help="显示版本")
    sub = parser.add_subparsers(dest="command")

    p_report = sub.add_parser("report", help="完整放量建议")
    _add_common(p_report)

    p_households = sub.add_parser("households", help="家庭批次分析")
    _add_common(p_households)

    p_site = sub.add_parser("site", help="共享网关站点视图")
    _add_common(p_site)

    p_bundle = sub.add_parser("bundle", help="已售套装客服视图")
    p_bundle.add_argument("bundle_id", help="套装编号，如 SB11")
    _add_common(p_bundle)
    return parser


def _print(payload, text: str, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print(text)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.version or args.command is None:
        print(f"本地分析工作区 {__version__}")
        return 0

    root = Path(args.data)
    if args.command in ("report", "households", "bundle"):
        data = maintenance.MaintenanceData.load(root)
        batch = maintenance.analyze_households(data)
    if args.command in ("report", "site"):
        site = site_mod.SharedSite.load(root)
        site_report = site.analyze()
        rechecks = site.recheck_report()
        what_ifs = site.permission_what_if()
        joint = site_mod.joint_recheck_coverage([site])

    if args.command == "report":
        bundles = [
            maintenance.bundle_view(data, batch, b["id"]) for b in data.sold_bundles
        ]
        payload = {
            "households": batch,
            "site": site_report,
            "site_rechecks": rechecks,
            "site_permission_what_if": what_ifs,
            "joint_recheck_coverage": joint,
            "bundles": bundles,
        }
        text = "\n\n".join(
            [
                report.render_households(batch),
                report.render_site(site_report, rechecks, what_ifs, joint),
                *[report.render_bundle(b) for b in bundles],
            ]
        )
        _print(payload, text, args.json)
    elif args.command == "households":
        _print(batch, report.render_households(batch), args.json)
    elif args.command == "site":
        payload = {
            "site": site_report,
            "rechecks": rechecks,
            "permission_what_if": what_ifs,
            "joint_recheck_coverage": joint,
        }
        _print(payload, report.render_site(site_report, rechecks, what_ifs, joint), args.json)
    elif args.command == "bundle":
        maintenance_ids = {b["id"] for b in data.sold_bundles}
        if args.bundle_id in maintenance_ids:
            view = maintenance.bundle_view(data, batch, args.bundle_id)
            _print(view, report.render_bundle(view), args.json)
        else:
            site = site_mod.SharedSite.load(root)
            site_report = site.analyze()
            view = site.bundle_view(site_report, args.bundle_id)
            _print(view, report.render_site_bundle(view), args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
