"""Capture structured scope cards from a source leaflet and its local copy."""
import json

from . import edition, translation
from .sources import record
from .store import rows


def ingest(db, events):
    made = 0
    with db:
        for e in events:
            if e['op'] != 'rule-card':
                raise ValueError('unknown rule card op ' + e['op'])
            if e['language'] != 'source' and not e['language']:
                raise ValueError('rule card language required')
            if record(db, e, 'rule-card', e['approval_no']):
                db.execute(
                    'INSERT INTO rule_card VALUES (?,?,?,?,?,?,?,?,?)',
                    (e['event_id'], e['approval_no'], str(e['edition']), e['language'],
                     e['section_id'], e['source_hash'], e['rule_key'],
                     json.dumps(e['scope'], ensure_ascii=False, sort_keys=True), e['action']))
                made += 1
        keys = {(e['approval_no'], str(e['edition']), e['language']) for e in events if e['language'] != 'source'}
        for approval_no, edition_no, language in keys:
            decide(db, approval_no, edition_no, language, max(e['event_time'] for e in events))
    return {'imported': made, 'verdicts': rows(db, 'SELECT * FROM rule_card_verdict ORDER BY approval_no,edition,language')}


def decide(db, approval_no, edition_no, language, as_of):
    source = edition.at(db, approval_no, edition_no, as_of)
    cards = rows(db,
        'SELECT c.*,e.event_time FROM rule_card c JOIN evidence e USING(event_id) '
        'WHERE c.approval_no=? AND c.edition=? AND c.language IN (?,"source") AND e.event_time<=? '
        'ORDER BY e.event_time DESC,c.event_id DESC',
        (approval_no, str(edition_no), language, as_of))
    selected = {}
    for card in cards:
        selected.setdefault((card['language'], card['section_id'], card['rule_key']), card)
    originals = [v for (lang, _, _), v in selected.items() if lang == 'source']
    if not originals:
        decision, reason = 'not_required', 'no_source_rule_cards'
    elif source['decision'] != 'trusted':
        decision, reason = 'blocked', 'source_not_trusted'
    elif translation.decide(db, approval_no, edition_no, language, as_of)['decision'] != 'released':
        decision, reason = 'blocked', 'language_not_released'
    else:
        section_hashes = json.loads(source['sections'])
        invalid_source = any(section_hashes.get(c['section_id']) != c['source_hash'] for c in originals)
        missing = any((language, c['section_id'], c['rule_key']) not in selected for c in originals)
        different = any(
            (selected[(language, c['section_id'], c['rule_key'])]['scope_json'],
             selected[(language, c['section_id'], c['rule_key'])]['action']) != (c['scope_json'], c['action'])
            for c in originals if (language, c['section_id'], c['rule_key']) in selected)
        if invalid_source:
            decision, reason = 'blocked', 'source_section_hash_mismatch'
        elif missing:
            decision, reason = 'blocked', 'translated_rule_missing'
        elif different:
            decision, reason = 'blocked', 'rule_scope_or_action_mismatch'
        else:
            decision, reason = 'aligned', 'all_rule_cards_equal'
    db.execute(
        'INSERT INTO rule_card_verdict VALUES (?,?,?,?,?) ON CONFLICT(approval_no,edition,language) '
        'DO UPDATE SET decision=excluded.decision,reason=excluded.reason',
        (approval_no, str(edition_no), language, decision, reason))
    return {'approval_no': approval_no, 'edition': str(edition_no), 'language': language,
            'decision': decision, 'reason': reason}
