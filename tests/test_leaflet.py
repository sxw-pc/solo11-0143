import json,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
import leaflet
from leaflet import edition,translation,package,report
from leaflet.store import connect
print('imported module:',leaflet.__file__)
assert Path(leaflet.__file__).resolve().is_relative_to(ROOT)

def fixture(name):return json.loads((ROOT/'fixtures'/'leaflet'/name).read_text(encoding='utf-8'))

class LeafletCases(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.db=connect(Path(self.tmp.name)/'test.sqlite')
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def good(self):
        edition.ingest(self.db,fixture('source_good.json'))
        translation.ingest(self.db,fixture('translation_good.json'))
        package.ingest(self.db,fixture('package_good.json'))

    def test_three_subsystems_publish(self):
        self.good();r=report.decide(self.db,'2026-11-17T10:00:00Z')
        self.assertEqual((r['editions'][0]['decision'],r['languages'][0]['decision'],r['packages'][0]['decision']),('trusted','released','published'))
        self.assertEqual(package.strength_mg('0.5 g'),500)

    def test_same_edition_conflict_freezes_language_and_package(self):
        self.good();edition.ingest(self.db,fixture('source_conflict.json'))
        r=report.decide(self.db,'2026-11-17T10:00:00Z')
        self.assertEqual((r['editions'][0]['decision'],r['languages'][0]['decision'],r['packages'][0]['decision']),('disputed','blocked','frozen'))

    def test_missing_translation_prevents_publication(self):
        edition.ingest(self.db,fixture('source_good.json'))
        translation.ingest(self.db,fixture('translation_missing.json'))
        sku=dict(fixture('package_good.json')[0],language='en')
        package.ingest(self.db,[sku])
        r=report.decide(self.db,'2026-11-17T10:00:00Z')
        self.assertEqual((r['languages'][0]['reason'],r['packages'][0]['reason']),('translated_sections_missing','language_not_released'))

    def test_translation_signoff_unlocks_existing_package(self):
        edition.ingest(self.db,fixture('source_good.json'))
        package.ingest(self.db,fixture('package_good.json'))
        self.assertEqual(report.decide(self.db,'2026-11-17T09:35:00Z')['packages'][0]['decision'],'frozen')
        translation.ingest(self.db,fixture('translation_good.json'))
        self.assertEqual(report.decide(self.db,'2026-11-17T10:00:00Z')['packages'][0]['decision'],'published')
        self.assertEqual([x['decision'] for x in report.decide(self.db,'2026-11-17T10:00:00Z')['publication_receipts']],['frozen','published'])

    def test_self_signoff_is_not_independent(self):
        edition.ingest(self.db,fixture('source_good.json'))
        translations=fixture('translation_good.json')
        translations[-1]=dict(translations[-1],reviewer='alice')
        translation.ingest(self.db,translations)
        self.assertEqual(translation.decide(self.db,'SYN-001','1','zh','2026-11-17T10:00:00Z')['reason'],'self_signoff')

    def test_expired_signoff_does_not_release_language(self):
        edition.ingest(self.db,fixture('source_good.json'))
        translation.ingest(self.db,fixture('translation_good.json'))
        self.assertEqual(translation.decide(self.db,'SYN-001','1','zh','2027-02-01T00:00:00Z')['reason'],'no_current_signoff')

    def test_formulation_mismatch_not_fixed_by_unit_conversion(self):
        self.good();package.ingest(self.db,fixture('package_mismatch.json'))
        r=report.decide(self.db,'2026-11-17T10:00:00Z')
        bad=next(x for x in r['packages'] if x['sku']=='SKU-BAD')
        self.assertEqual((bad['decision'],bad['reason']),('frozen','form_or_strength_mismatch'))

    def test_market_mismatch_is_distinct_from_translation(self):
        self.good();e=dict(fixture('package_good.json')[0],event_id='sku-market',sku='SKU-MARKET',market='EU')
        package.ingest(self.db,[e])
        r=report.decide(self.db,'2026-11-17T10:00:00Z')
        bad=next(x for x in r['packages'] if x['sku']=='SKU-MARKET')
        self.assertEqual(bad['reason'],'market_not_authorized')

    def test_targeted_withdrawal_freezes_only_original_edition(self):
        self.good()
        e2=dict(fixture('source_good.json')[1],event_id='edition-2',edition='2',predecessor='1',document_hash='doc-h2',event_time='2026-11-17T09:40:00Z')
        edition.ingest(self.db,[e2])
        withdraw={'event_id':'withdraw-1','op':'withdraw','approval_no':'SYN-001','edition':'1','event_time':'2026-11-17T09:50:00Z'}
        edition.ingest(self.db,[withdraw])
        r=report.decide(self.db,'2026-11-17T10:00:00Z')
        states={x['edition']:x['decision'] for x in r['editions']}
        self.assertEqual(states,{'1':'withdrawn','2':'trusted'})
        self.assertEqual(r['packages'][0]['decision'],'frozen')
        self.assertEqual(r['replacement'][0]['decision'],'urgent')

    def test_as_of_before_withdrawal_preserves_then_current_freezes(self):
        self.good()
        withdraw={'event_id':'withdraw-late','op':'withdraw','approval_no':'SYN-001','edition':'1','event_time':'2026-11-17T09:50:00Z'}
        edition.ingest(self.db,[withdraw])
        earlier=report.decide(self.db,'2026-11-17T09:40:00Z')
        current=report.decide(self.db,'2026-11-17T10:00:00Z')
        self.assertEqual((earlier['editions'][0]['decision'],earlier['packages'][0]['decision']),('trusted','published'))
        self.assertEqual((current['editions'][0]['decision'],current['packages'][0]['decision']),('withdrawn','frozen'))

    def test_new_edition_needs_new_translation_and_binding(self):
        self.good()
        e2=dict(fixture('source_good.json')[1],event_id='edition-2',edition='2',predecessor='1',document_hash='doc-h2',event_time='2026-11-17T09:40:00Z')
        edition.ingest(self.db,[e2])
        sku=dict(fixture('package_good.json')[0],event_id='sku-2',edition='2',event_time='2026-11-17T09:45:00Z')
        package.ingest(self.db,[sku])
        self.assertEqual(report.decide(self.db,'2026-11-17T10:00:00Z')['packages'][0]['reason'],'language_not_released')

    def test_untrusted_publisher_is_rejected(self):
        e=dict(fixture('source_good.json')[1],publisher='other')
        edition.ingest(self.db,[e])
        self.assertEqual(edition.get(self.db,'SYN-001','1')['decision'],'untrusted')

    def test_duplicate_id_new_content_rolls_back(self):
        self.good();changed=dict(fixture('package_good.json')[0],form='syrup')
        with self.assertRaises(ValueError):package.ingest(self.db,[dict(changed,event_id='new-sku',sku='new'),changed])
        self.assertIsNone(self.db.execute("SELECT event_id FROM evidence WHERE event_id='new-sku'").fetchone())

if __name__=='__main__':unittest.main()
