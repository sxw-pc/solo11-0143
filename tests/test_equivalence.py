import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leaflet import edition, equivalence, package, report, rulecard, translation
from leaflet.store import connect


def fixture(name):
    return json.loads((ROOT / 'fixtures' / 'leaflet' / name).read_text(encoding='utf-8'))


def card(event_id, language, section, rule_key, scope, action,
         approval='SYN-002', source_hash='h2-contra-1', t='2026-11-18T09:25:00Z'):
    return {'event_id': event_id, 'op': 'rule-card', 'approval_no': approval,
            'edition': '1', 'language': language, 'section_id': section,
            'source_hash': source_hash, 'rule_key': rule_key, 'scope': scope,
            'action': action, 'event_time': t}


def exception(event_id, language, section, name, scope,
              approval='SYN-002', t='2026-11-18T09:24:00Z'):
    return {'event_id': event_id, 'op': 'rule-exception', 'approval_no': approval,
            'edition': '1', 'language': language, 'section_id': section,
            'name': name, 'scope': scope, 'event_time': t}


def sku(event_id, sku_id, market, approval='SYN-002', form='tablet',
        language='zh', t='2026-11-18T09:30:00Z'):
    return {'event_id': event_id, 'op': 'sku', 'sku': sku_id, 'approval_no': approval,
            'edition': '1', 'form': form, 'strength': '500 mg', 'market': market,
            'language': language, 'event_time': t}


def contract(event_id, contract_id, markets, approval='SYN-002',
             form='tablet', language='zh', t='2026-11-18T09:31:00Z'):
    return {'event_id': event_id, 'op': 'print-contract', 'contract_id': contract_id,
            'approval_no': approval, 'edition': '1', 'language': language,
            'form': form, 'markets': markets, 'event_time': t}


class EquivalenceBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = connect(Path(self.tmp.name) / 'equiv.sqlite')
        edition.ingest(self.db, fixture('source_two_markets.json'))
        translation.ingest(self.db, fixture('translation_two_markets.json'))
        self.at = '2026-11-18T10:00:00Z'

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def certify(self, cards, markets=('CN', 'JP'), receiver='edition',
                receiver_id='SYN-002:1:zh', exceptions=()):
        rulecard.ingest(self.db, list(exceptions) + list(cards))
        return rulecard.certify(self.db, 'SYN-002', '1', 'zh', self.at,
                                receiver, receiver_id, list(markets))

    def split_merge_cards(self):
        return [
            card('s1', 'source', 'contraindication', 'age-low',
                 {'all': [{'age_years': [0, 5]}, {'market': 'CN'}]}, 'exclude'),
            card('s2', 'source', 'contraindication', 'age-high',
                 {'all': [{'age_years': [6, 12]}, {'market': 'CN'}]}, 'exclude'),
            card('t1', 'zh', 'contraindication', 'age-band',
                 {'all': [{'age_years': [0, 12]}, {'market': 'CN'}]}, 'exclude',
                 t='2026-11-18T09:26:00Z'),
        ]


class SplitMergeAndOrder(EquivalenceBase):
    def test_split_source_equals_merged_translation(self):
        entry = self.certify(self.split_merge_cards())
        self.assertEqual(entry['decision'], 'equivalent')
        # Whole domain covered, including the open age tail on both markets.
        bands = {(w['market'], tuple(w['age_years'])) for w in entry['witnesses']}
        self.assertIn(('CN', (13, None)), bands)
        self.assertIn(('JP', (13, None)), bands)
        cn_excl = [w for w in entry['witnesses'] if w['market'] == 'CN' and w['actions'] == ['exclude']]
        self.assertEqual({tuple(w['age_years']) for w in cn_excl}, {(0, 5), (6, 12)})
        # JP has no restriction on either side: empty action function everywhere.
        self.assertTrue(all(w['actions'] == [] for w in entry['witnesses']
                            if w['market'] == 'JP'))

    def test_order_and_rule_keys_do_not_change_conclusion(self):
        cards = self.split_merge_cards()
        first = self.certify(cards)
        # Re-key and reverse the import order: same semantic content.
        reordered = [
            dict(cards[2], rule_key='renamed-merged', event_time='2026-11-18T09:27:00Z'),
            dict(cards[1], rule_key='renamed-high'),
            dict(cards[0], rule_key='renamed-low'),
        ]
        second = rulecard.certify(self.db, 'SYN-002', '1', 'zh', self.at,
                                  'edition', 'rekeyed', ['CN', 'JP'])
        self.assertEqual(second['decision'], first['decision'])
        self.assertEqual(second['reason'], first['reason'])


class OpenAgeTail(EquivalenceBase):
    def test_open_tail_difference_is_not_skipped(self):
        cards = [
            card('s1', 'source', 'contraindication', 'k',
                 {'age_years': [13, None]}, 'exclude'),
            card('t1', 'zh', 'contraindication', 'k',
                 {'age_years': [13, 65]}, 'exclude', t='2026-11-18T09:26:00Z'),
        ]
        entry = self.certify(cards)
        self.assertEqual(entry['decision'], 'different')
        tails = [w for w in entry['witnesses']
                 if w['type'] == 'action_mismatch' and w['age_years'][1] is None]
        self.assertEqual(len(tails), 2)  # one per market
        # Witness is replayable: concrete condition plus both action sets.
        self.assertEqual(tails[0]['source_actions'], ['exclude'])
        self.assertEqual(tails[0]['translated_actions'], [])

    def test_equivalent_open_tails_match(self):
        cards = [
            card('s1', 'source', 'contraindication', 'k', {'age_years': [13, None]}, 'exclude'),
            card('t1', 'zh', 'contraindication', 'k2', {'age_years': [13, None]}, 'exclude',
                 t='2026-11-18T09:26:00Z'),
        ]
        self.assertEqual(self.certify(cards)['decision'], 'equivalent')


class NamedExceptions(EquivalenceBase):
    def test_named_exception_equivalence(self):
        cards = [
            exception('ex-s', 'source', 'contraindication', 'paediatric',
                      {'age_years': [0, 12]}),
            exception('ex-t', 'zh', 'contraindication', 'paediatric',
                      {'age_years': [0, 12]}),
            card('s1', 'source', 'contraindication', 'base',
                 {'all': [{'market': 'CN'}, {'not': {'except': 'paediatric'}}]}, 'exclude'),
            card('t1', 'zh', 'contraindication', 'base',
                 {'all': [{'market': 'CN'}, {'not': {'except': 'paediatric'}}]}, 'exclude',
                 t='2026-11-18T09:26:00Z'),
        ]
        self.assertEqual(self.certify(cards)['decision'], 'equivalent')

    def test_exception_cycle_is_pending_evidence(self):
        cards = [
            exception('ex-a', 'source', 'contraindication', 'a', {'except': 'b'}),
            exception('ex-b', 'source', 'contraindication', 'b', {'except': 'a'}),
            card('s1', 'source', 'contraindication', 'base',
                 {'all': [{'market': 'CN'}, {'not': {'except': 'a'}}]}, 'exclude'),
            card('t1', 'zh', 'contraindication', 'base',
                 {'all': [{'market': 'CN'}, {'not': {'except': 'a'}}]}, 'exclude',
                 t='2026-11-18T09:26:00Z'),
        ]
        entry = self.certify(cards)
        self.assertEqual(entry['decision'], 'pending')
        self.assertTrue(any(w['reason'] == 'exception_cycle' for w in entry['witnesses']))

    def test_undefined_exception_is_pending(self):
        cards = [
            card('s1', 'source', 'contraindication', 'base',
                 {'all': [{'market': 'CN'}, {'not': {'except': 'ghost'}}]}, 'exclude'),
            card('t1', 'zh', 'contraindication', 'base',
                 {'all': [{'market': 'CN'}, {'not': {'except': 'ghost'}}]}, 'exclude',
                 t='2026-11-18T09:26:00Z'),
        ]
        entry = self.certify(cards)
        self.assertEqual(entry['decision'], 'pending')
        self.assertTrue(any(w['reason'] == 'undefined_exception_reference'
                            for w in entry['witnesses']))

    def test_duplicate_exception_definition_is_pending(self):
        cards = [
            exception('ex-1', 'source', 'contraindication', 'dup', {'age_years': [0, 3]}),
            exception('ex-2', 'source', 'contraindication', 'dup', {'age_years': [0, 4]},
                      t='2026-11-18T09:24:30Z'),
            card('s1', 'source', 'contraindication', 'base',
                 {'all': [{'market': 'CN'}, {'not': {'except': 'dup'}}]}, 'exclude'),
            card('t1', 'zh', 'contraindication', 'base',
                 {'all': [{'market': 'CN'}, {'not': {'except': 'dup'}}]}, 'exclude',
                 t='2026-11-18T09:26:00Z'),
        ]
        entry = self.certify(cards)
        self.assertEqual(entry['decision'], 'pending')
        self.assertTrue(any(w['reason'] == 'duplicate_exception_definition'
                            for w in entry['witnesses']))

    def test_repeated_exception_references_are_allowed(self):
        # The same named exception referenced from multiple places must not
        # look like a cycle or a duplicate definition.
        cards = [
            exception('ex-s', 'source', 'contraindication', 'kids', {'age_years': [0, 12]}),
            exception('ex-t', 'zh', 'contraindication', 'kids', {'age_years': [0, 12]}),
            card('s1', 'source', 'contraindication', 'base',
                 {'all': [{'market': 'CN'},
                          {'any': [{'not': {'except': 'kids'}}, {'except': 'kids'}]}]},
                 'allow'),
            card('t1', 'zh', 'contraindication', 'base',
                 {'all': [{'market': 'CN'},
                          {'any': [{'except': 'kids'}, {'not': {'except': 'kids'}}]}]},
                 'allow', t='2026-11-18T09:26:00Z'),
        ]
        self.assertEqual(self.certify(cards)['decision'], 'equivalent')

    def test_or_split_into_two_cards_is_equivalent(self):
        cards = [
            card('s1', 'source', 'contraindication', 'union',
                 {'all': [{'age_years': [0, 5]},
                          {'any': [{'market': 'CN'}, {'market': 'JP'}]}]}, 'exclude'),
            card('t1', 'zh', 'contraindication', 'cn-part',
                 {'all': [{'age_years': [0, 5]}, {'market': 'CN'}]}, 'exclude',
                 t='2026-11-18T09:26:00Z'),
            card('t2', 'zh', 'contraindication', 'jp-part',
                 {'all': [{'age_years': [0, 5]}, {'market': 'JP'}]}, 'exclude',
                 t='2026-11-18T09:26:00Z'),
        ]
        entry = self.certify(cards)
        self.assertEqual(entry['decision'], 'equivalent')
        self.assertIn('action_functions_equal_over_domain', entry['reason'])


class ConflictsAndUnknownValues(EquivalenceBase):
    def test_opposite_actions_same_condition_is_pending_not_last_writer(self):
        cards = [
            card('s1', 'source', 'contraindication', 'k1', {'market': 'CN'}, 'exclude'),
            card('s2', 'source', 'contraindication', 'k2', {'market': 'CN'}, 'allow',
                 t='2026-11-18T09:25:30Z'),
            card('t1', 'zh', 'contraindication', 'k1', {'market': 'CN'}, 'exclude',
                 t='2026-11-18T09:26:00Z'),
        ]
        entry = self.certify(cards)
        self.assertEqual(entry['decision'], 'pending')
        self.assertEqual(entry['reason'], 'conflicting_actions_overlap')
        self.assertTrue(all(w['type'] == 'conflicting_actions' or
                            w['type'] == 'action_mismatch' for w in entry['witnesses']))

    def test_unknown_market_cannot_join_enum(self):
        cards = [
            card('s1', 'source', 'contraindication', 'k', {'market': 'ZZ'}, 'exclude'),
            card('t1', 'zh', 'contraindication', 'k', {'market': 'ZZ'}, 'exclude',
                 t='2026-11-18T09:26:00Z'),
        ]
        entry = self.certify(cards)
        self.assertEqual(entry['decision'], 'pending')
        self.assertTrue(any(w['reason'] == 'unknown_market_not_in_authorized_set'
                            for w in entry['witnesses']))

    def test_unknown_form_cannot_join_enum(self):
        cards = [
            card('s1', 'source', 'contraindication', 'k', {'form': 'syrup'}, 'exclude'),
            card('t1', 'zh', 'contraindication', 'k', {'form': 'syrup'}, 'exclude',
                 t='2026-11-18T09:26:00Z'),
        ]
        entry = self.certify(cards)
        self.assertEqual(entry['decision'], 'pending')
        self.assertTrue(any(w['reason'] == 'unknown_form_not_in_authorized_set'
                            for w in entry['witnesses']))

    def test_invalid_age_band_is_pending(self):
        cards = [
            card('s1', 'source', 'contraindication', 'k', {'age_years': [12, 5]}, 'exclude'),
            card('t1', 'zh', 'contraindication', 'k', {'age_years': [12, 5]}, 'exclude',
                 t='2026-11-18T09:26:00Z'),
        ]
        entry = self.certify(cards)
        self.assertEqual(entry['decision'], 'pending')
        self.assertTrue(any(w['reason'] == 'invalid_age_band' for w in entry['witnesses']))


class ReceiverProjections(EquivalenceBase):
    def _load_split_scenario(self):
        rulecard.ingest(self.db, fixture('rule_cards_split.json'))
        package.ingest(self.db, fixture('package_two_markets.json'))

    def test_electronic_sku_passes_on_market_projection(self):
        self._load_split_scenario()
        # The translated-only JP allowance card lands afterwards.
        rulecard.ingest(self.db, fixture('rule_cards_extra_market.json'))
        result = report.decide(self.db, self.at)
        sku_row = next(p for p in result['packages'] if p['sku'] == 'SKU-CN-2')
        self.assertEqual((sku_row['decision'], sku_row['reason']),
                         ('published', 'all_dependencies_valid'))
        contract_row = next(c for c in result['contracts'] if c['contract_id'] == 'PRINT-1')
        # JP: source has no card, translation adds an "allow" card -- a real
        # action-function difference over the combined print domain.
        self.assertEqual(contract_row['decision'], 'frozen')
        self.assertEqual(contract_row['reason'], 'rule_cards_not_equivalent')
        full = [c for c in result['certifications']
                if c['receiver_type'] == 'print_contract' and c['receiver_id'] == 'PRINT-1']
        self.assertTrue(any(w['type'] == 'action_mismatch' and w['market'] == 'JP'
                            for w in full[-1]['witnesses']))
        # Both receiver types keep separate certification bases.
        receivers = {(c['receiver_type'], c['receiver_id']) for c in result['certifications']}
        self.assertIn(('sku', 'SKU-CN-2'), receivers)
        self.assertIn(('print_contract', 'PRINT-1'), receivers)
        self.assertIn('edition', {r[0] for r in receivers})
        # Projection never masquerades as full-edition equivalence: the
        # earliest edition certification predates the JP card and is
        # equivalent; the latest one, over both markets, is not.
        edition_cert = [c for c in result['certifications'] if c['receiver_type'] == 'edition']
        self.assertEqual(edition_cert[0]['decision'], 'equivalent')
        self.assertNotEqual(edition_cert[-1]['decision'], 'equivalent')
        self.assertEqual(edition_cert[-1]['domain'], ['CN', 'JP'])
        sku_cert = [c for c in result['certifications']
                    if c['receiver_type'] == 'sku' and c['receiver_id'] == 'SKU-CN-2']
        self.assertEqual(sku_cert[-1]['domain'], ['CN'])

    def test_print_contract_pending_when_conflict_lives_outside_projection(self):
        # Two translated cards with opposite actions on the same JP condition
        # are pending evidence: no last-writer-wins, and the SKU's CN-only
        # projection cannot rescue the combined print delivery.
        rulecard.ingest(self.db, fixture('rule_cards_split.json'))
        rulecard.ingest(self.db, [
            card('zh-jp-allow', 'zh', 'contraindication', 'jp-allow',
                 {'market': 'JP'}, 'allow', t='2026-11-18T09:28:00Z'),
            card('zh-jp-exclude', 'zh', 'contraindication', 'jp-exclude',
                 {'market': 'JP'}, 'exclude', t='2026-11-18T09:28:30Z'),
        ])
        package.ingest(self.db, [
            sku('sku-cn-3', 'SKU-CN-3', 'CN'),
            contract('pc-2', 'PRINT-2', ['CN', 'JP']),
        ])
        result = report.decide(self.db, self.at)
        sku_row = next(p for p in result['packages'] if p['sku'] == 'SKU-CN-3')
        self.assertEqual(sku_row['decision'], 'published')
        contract_row = next(c for c in result['contracts'] if c['contract_id'] == 'PRINT-2')
        self.assertEqual((contract_row['decision'], contract_row['reason']),
                         ('frozen', 'rule_cards_pending_evidence'))

    def test_print_contract_unauthorized_market_freezes(self):
        rulecard.ingest(self.db, fixture('rule_cards_split.json'))
        package.ingest(self.db, [contract('pc-x', 'PRINT-X', ['CN', 'EU'])])
        row = package.decide_contract(self.db, 'PRINT-X', self.at)
        self.assertEqual((row['decision'], row['reason']),
                         ('frozen', 'market_not_authorized'))

    def test_missing_translated_card_is_a_difference_not_separate_verdict(self):
        # Source only: the translated action function is empty everywhere.
        entry = self.certify([
            card('s1', 'source', 'contraindication', 'k',
                 {'all': [{'age_years': [0, 12]}, {'market': 'CN'}]}, 'exclude')])
        self.assertEqual(entry['decision'], 'different')
        self.assertTrue(all(w['type'] == 'action_mismatch' for w in entry['witnesses']))
        # The JP region is untouched by the source card, so it is not a witness.
        self.assertTrue(all(w['market'] == 'CN' for w in entry['witnesses']))


class HistoryAndWithdrawal(EquivalenceBase):
    def test_history_is_immutable_and_point_in_time(self):
        rulecard.ingest(self.db, self.split_merge_cards())
        first = report.decide(self.db, '2026-11-18T09:40:00Z')
        before = [c for c in first['certifications'] if c['receiver_type'] == 'edition']
        self.assertEqual(before[-1]['decision'], 'equivalent')
        # Withdraw the edition: later adjudications are blocked...
        edition.ingest(self.db, [
            {'event_id': 'withdraw-2-1', 'op': 'withdraw', 'approval_no': 'SYN-002',
             'edition': '1', 'event_time': '2026-11-18T09:50:00Z'}])
        after = report.decide(self.db, self.at)
        current = [c for c in after['certifications'] if c['receiver_type'] == 'edition']
        self.assertEqual(current[-1]['decision'], 'blocked')
        self.assertEqual(current[-1]['reason'], 'source_not_trusted')
        # ...but the historical equivalent verdict survives with its basis.
        old = rulecard.history(self.db, '2026-11-18T09:40:00Z')
        old_edition = [c for c in old if c['receiver_type'] == 'edition']
        self.assertEqual(old_edition[-1]['decision'], 'equivalent')
        self.assertTrue(old_edition[-1]['source_summary'])
        self.assertTrue(old_edition[-1]['witnesses'])
        # Current-value table reflects today; history cannot be overwritten.
        verdict = next(r for r in after['rule_cards'] if r['language'] == 'zh')
        self.assertEqual(verdict['decision'], 'blocked')

    def test_cards_invisible_at_as_of_cannot_change_then_conclusion(self):
        rulecard.ingest(self.db, self.split_merge_cards())
        early = rulecard.certify(self.db, 'SYN-002', '1', 'zh', '2026-11-18T09:27:00Z',
                                 'edition', 'early', ['CN', 'JP'])
        self.assertEqual(early['decision'], 'equivalent')
        # A contradicting translated card arrives later; queries at the old
        # instant must still reproduce the old equivalence with old inputs.
        rulecard.ingest(self.db, [
            card('zh-late', 'zh', 'contraindication', 'late',
                 {'market': 'CN'}, 'allow', t='2026-11-18T09:35:00Z')])
        replayed = rulecard.certify(self.db, 'SYN-002', '1', 'zh', '2026-11-18T09:27:00Z',
                                    'edition', 'early', ['CN', 'JP'])
        self.assertEqual(replayed['input_hash'], early['input_hash'])
        self.assertEqual(replayed['decision'], 'equivalent')
        current = rulecard.certify(self.db, 'SYN-002', '1', 'zh', self.at,
                                   'edition', 'now', ['CN', 'JP'])
        self.assertNotEqual(current['decision'], 'equivalent')

    def test_recompute_on_later_source_card_uses_edition_at(self):
        rulecard.ingest(self.db, self.split_merge_cards())
        self.assertEqual(
            rulecard.decide(self.db, 'SYN-002', '1', 'zh', '2026-11-18T09:40:00Z')
            ['verdict_decision'], 'aligned')
        edition.ingest(self.db, [
            {'event_id': 'withdraw-2-1b', 'op': 'withdraw', 'approval_no': 'SYN-002',
             'edition': '1', 'event_time': '2026-11-18T09:50:00Z'}])
        redecided = rulecard.decide(self.db, 'SYN-002', '1', 'zh', self.at)
        self.assertEqual(redecided['decision'], 'blocked')
        package.ingest(self.db, [sku('sku-cn-x', 'SKU-CN-X', 'CN')])
        binding = package.decide(self.db, 'SKU-CN-X', self.at)
        self.assertEqual(binding['decision'], 'frozen')
        self.assertEqual(binding['reason'], 'edition_not_trusted')

    def test_duplicate_import_and_restart_reproduce(self):
        cards = self.split_merge_cards()
        self.assertEqual(rulecard.ingest(self.db, cards)['imported'], 3)
        self.assertEqual(rulecard.ingest(self.db, cards)['imported'], 0)
        # Pin the adjudication instant so repeated imports and restarts hash
        # the same certification input instead of opening new history rows.
        pinned = '2026-11-18T09:40:00Z'
        rulecard.decide(self.db, 'SYN-002', '1', 'zh', pinned)
        rows_before = rulecard.history(self.db)
        self.db.close()
        self.db = connect(Path(self.tmp.name) / 'equiv.sqlite')
        self.assertEqual(rulecard.ingest(self.db, cards)['imported'], 0)
        rulecard.decide(self.db, 'SYN-002', '1', 'zh', pinned)
        rows_after = rulecard.history(self.db)
        self.assertEqual(
            [r['input_hash'] for r in rows_after],
            [r['input_hash'] for r in rows_before])
        self.assertEqual(
            rulecard.decide(self.db, 'SYN-002', '1', 'zh', pinned)['decision'],
            'equivalent')


class CliEndToEnd(unittest.TestCase):
    def test_cli_runs_full_chain_and_keeps_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / 'cli.sqlite')
            root = ROOT

            def run(*parts):
                env = dict(__import__('os').environ,
                           PYTHONPATH=str(ROOT / 'src'))
                proc = subprocess.run(
                    [sys.executable, '-B', '-m', 'leaflet', *parts, '--db', db_path],
                    cwd=root, capture_output=True, text=True, check=True, env=env)
                return json.loads(proc.stdout)

            run('init')
            run('import-source', '--input', 'fixtures/leaflet/source_two_markets.json')
            run('align', '--input', 'fixtures/leaflet/translation_two_markets.json')
            run('capture-rule-cards', '--input', 'fixtures/leaflet/rule_cards_split.json')
            run('capture-rule-cards', '--input', 'fixtures/leaflet/rule_cards_extra_market.json')
            run('map-package', '--input', 'fixtures/leaflet/package_two_markets.json')
            result = run('report', '--as-of', '2026-11-18T10:00:00Z')
            sku_row = next(p for p in result['packages'] if p['sku'] == 'SKU-CN-2')
            contract_row = next(c for c in result['contracts'] if c['contract_id'] == 'PRINT-1')
            self.assertEqual(sku_row['decision'], 'published')
            self.assertEqual(contract_row['decision'], 'frozen')
            self.assertTrue(result['certifications'])
            self.assertTrue(result['contract_receipts'])


if __name__ == '__main__':
    unittest.main()
