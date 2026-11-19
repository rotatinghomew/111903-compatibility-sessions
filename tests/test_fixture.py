"""检查合成资料可读。"""
import json
from pathlib import Path
import unittest

class FixtureTest(unittest.TestCase):
    def test_sample_is_json_object(self):
        path = Path(__file__).parents[1] / "data" / "sample.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsInstance(value, dict)
        self.assertTrue(value)

    def test_dispatch_export_references(self):
        path = Path(__file__).parents[1] / "data" / "maintenance_windows.json"
        export = json.loads(path.read_text(encoding="utf-8"))
        states = set(export["states"])
        households = {record["id"]: record for record in export["households"]}
        changes = {(record["from"], record["to"]) for record in export["changes"]}
        trials = {record["id"] for record in export["trials"]}
        self.assertEqual(len(trials), len(export["trials"]))
        for source, target in changes:
            self.assertLessEqual({source, target}, states)
        for record in export["trials"]:
            self.assertIn(record["state"], states)
            self.assertIn(record["function"], export["protocol"])
        for record in export["dispatches"]:
            self.assertIn((record["from"], record["to"]), changes)
            self.assertIn(record["window"], households[record["household"]]["windows"])
            self.assertIn(record["window"], export["windows"])
        for record in export["corrections"]:
            self.assertIn(record["trial"], trials)

if __name__ == "__main__":
    unittest.main()
