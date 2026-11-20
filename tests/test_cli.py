"""CLI 冒烟测试。"""
import io
import json
import unittest
from contextlib import redirect_stdout

from _util import DATA, load_module

cli = load_module("cli")


def run(argv):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(argv)
    return code, out.getvalue()


class CliTest(unittest.TestCase):
    def test_version_default(self):
        code, out = run([])
        self.assertEqual(code, 0)
        self.assertIn("本地分析工作区", out)

    def test_report_text(self):
        code, out = run(["report", "--data", str(DATA)])
        self.assertEqual(code, 0)
        self.assertIn("家庭批次", out)
        self.assertIn("共享站点 BLD-4", out)
        self.assertIn("已售套装 SB11", out)

    def test_report_json_is_machine_readable(self):
        code, out = run(["report", "--data", str(DATA), "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertIn("households", payload)
        self.assertIn("site", payload)
        self.assertEqual(payload["site"]["batches"]["commitment_gaps"], ["U43"])

    def test_households_and_site_and_bundle(self):
        for argv in (["households"], ["site"], ["bundle", "SB11"], ["bundle", "PB43"]):
            code, out = run([*argv, "--data", str(DATA)])
            self.assertEqual(code, 0, argv)
            self.assertTrue(out.strip(), argv)


if __name__ == "__main__":
    unittest.main()
