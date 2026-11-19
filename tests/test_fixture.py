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

    def test_partner_calendar_links_sold_bundles_and_equipment(self):
        root = Path(__file__).parents[1] / 'data'
        service = json.loads((root / 'maintenance_windows.json').read_text(encoding='utf-8'))
        partner = json.loads((root / 'partner_calendar.json').read_text(encoding='utf-8'))
        reserve, = partner['equipment_bookings']
        slots = {row['id']: row for row in partner['windows']}
        self.assertEqual(set(slots), set(service['windows']))
        self.assertEqual(reserve['equipment'], partner['equipment'][0]['id'])
        self.assertGreater(reserve['earliest_release'], slots[reserve['from_window']]['end'])
        self.assertTrue(any(row['id'] == reserve['reservation'] for row in service['dispatches']))
        bundle, = partner['sold_bundles']
        correction, = service['corrections']
        self.assertIn(correction['trial'], bundle['cited_trials'])
        self.assertLess(bundle['contracted_at'], correction['known_at'])

    def test_gateway_census_and_receipts_share_physical_asset_ids(self):
        path = Path(__file__).parents[1] / 'data' / 'shared_gateway.json'
        site = json.loads(path.read_text(encoding='utf-8'))
        homes = {row['id'] for row in site['homes']}
        gateways = {row['id'] for row in site['gateways']}
        operations = {row['id']: row for row in site['operations']}
        windows = {row['id']: row for row in site['windows']}
        orders = {row['id']: row for row in site['work_orders']}
        for row in site['inventory_notices'] + site['initial_connections']:
            self.assertIn(row['home'], homes)
            self.assertIn(row['gateway'], gateways)
        for row in site['permissions']:
            self.assertLessEqual(set(row['windows']), windows.keys())
            self.assertLessEqual(set(row['operations']), {op['kind'] for op in operations.values()})
        for receipt in site['receipts']:
            order = orders[receipt['work_order']]
            window = windows[order['window']]
            self.assertLessEqual(window['start'], order['started'])
            self.assertLess(order['started'], receipt['known_at'])
            self.assertIn(receipt['gateway'], gateways)
            self.assertEqual(operations[order['operation']]['kind'], 'move_home')

if __name__ == "__main__":
    unittest.main()
