import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leaflet import edition, package, report, rulecard, translation
from leaflet.store import connect


def fixture(name):
    return json.loads((ROOT / 'fixtures' / 'leaflet' / name).read_text(encoding='utf-8'))


class RuleCardCapture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = connect(Path(self.tmp.name) / 'capture.sqlite')
        edition.ingest(self.db, fixture('source_good.json'))
        translation.ingest(self.db, fixture('translation_good.json'))
        package.ingest(self.db, fixture('package_good.json'))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_cards_gate_existing_sku(self):
        at = '2026-11-17T10:00:00Z'
        self.assertEqual(report.decide(self.db, at)['packages'][0]['decision'], 'published')
        rulecard.ingest(self.db, fixture('rule_cards_good.json'))
        now = report.decide(self.db, at)
        self.assertEqual(now['rule_cards'][0]['decision'], 'aligned')
        self.assertEqual(now['packages'][0]['decision'], 'published')
        rulecard.ingest(self.db, fixture('rule_cards_mismatch.json'))
        changed = report.decide(self.db, at)
        self.assertEqual(changed['rule_cards'][0]['decision'], 'blocked')
        self.assertEqual(changed['packages'][0]['decision'], 'frozen')
        self.assertEqual(changed['publication_receipts'][-1]['decision'], 'frozen')

    def test_restart_and_idempotence(self):
        cards = fixture('rule_cards_good.json')
        self.assertEqual(rulecard.ingest(self.db, cards)['imported'], 2)
        self.db.close()
        self.db = connect(Path(self.tmp.name) / 'capture.sqlite')
        self.assertEqual(rulecard.ingest(self.db, cards)['imported'], 0)
        self.assertEqual(report.decide(self.db, '2026-11-17T10:00:00Z')['rule_cards'][0]['decision'], 'aligned')


if __name__ == '__main__':
    unittest.main()
