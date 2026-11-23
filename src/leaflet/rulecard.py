"""Capture structured scope cards from a source leaflet and its local copy.

Cards may split or merge the same restriction semantically; rule keys and
import order never drive the conclusion. Equivalence is adjudicated over the
whole applicable domain (edition markets x form x integer ages, including the
open age tail) by :mod:`leaflet.equivalence`. Every adjudication appends an
immutable :class:`rule_card_certification` row; ``rule_card_verdict`` remains
the overwritable current-value table.
"""
import json

from . import edition, equivalence, translation
from .sources import record
from .store import rows


def ingest(db, events):
    made = 0
    with db:
        for e in events:
            if e['op'] == 'rule-card':
                if e['language'] != 'source' and not e['language']:
                    raise ValueError('rule card language required')
                if record(db, e, 'rule-card', e['approval_no']):
                    db.execute(
                        'INSERT INTO rule_card VALUES (?,?,?,?,?,?,?,?,?)',
                        (e['event_id'], e['approval_no'], str(e['edition']), e['language'],
                         e['section_id'], e['source_hash'], e['rule_key'],
                         json.dumps(e['scope'], ensure_ascii=False, sort_keys=True), e['action']))
                    made += 1
            elif e['op'] == 'rule-exception':
                if not e['language'] or not e['name']:
                    raise ValueError('rule exception language and name required')
                if record(db, e, 'rule-exception', e['approval_no']):
                    db.execute(
                        'INSERT INTO rule_exception VALUES (?,?,?,?,?,?,?)',
                        (e['event_id'], e['approval_no'], str(e['edition']), e['language'],
                         e['section_id'], e['name'],
                         json.dumps(e['scope'], ensure_ascii=False, sort_keys=True)))
                    made += 1
            else:
                raise ValueError('unknown rule card op ' + e['op'])
        keys = {(e['approval_no'], str(e['edition']), e['language'])
                for e in events if e['op'] == 'rule-card' and e['language'] != 'source'}
        for approval_no, edition_no, language in keys:
            decide(db, approval_no, edition_no, language,
                   max(e['event_time'] for e in events))
    return {'imported': made,
            'verdicts': rows(db, 'SELECT * FROM rule_card_verdict ORDER BY approval_no,edition,language'),
            'certifications': history(db)}


def _visible_rows(db, approval_no, edition_no, language, as_of):
    cards = rows(db,
        'SELECT c.*,e.event_time FROM rule_card c JOIN evidence e USING(event_id) '
        'WHERE c.approval_no=? AND c.edition=? AND c.language IN (?,"source") AND e.event_time<=? '
        'ORDER BY e.event_time DESC,c.event_id DESC',
        (approval_no, str(edition_no), language, as_of))
    exceptions = rows(db,
        'SELECT x.*,e.event_time FROM rule_exception x JOIN evidence e USING(event_id) '
        'WHERE x.approval_no=? AND x.edition=? AND x.language IN (?,"source") AND e.event_time<=? '
        'ORDER BY e.event_time DESC,x.event_id DESC',
        (approval_no, str(edition_no), language, as_of))
    return cards, exceptions


def certify(db, approval_no, edition_no, language, as_of, receiver_type,
            receiver_id, receiver_markets, adjudicated_at=None):
    """Run one equivalence certification and append its immutable history row.

    The full-edition verdict uses every authorized market; a receiver
    (electronic SKU or print contract) projects onto its own market set. Each
    receiver keeps its own certification basis; a projected pass never
    overwrites the full-edition conclusion.
    """
    adjudicated_at = adjudicated_at or as_of
    source = edition.at(db, approval_no, edition_no, as_of)
    cards, exceptions = _visible_rows(db, approval_no, edition_no, language, as_of)
    authorized = json.loads(source['markets']) if source['decision'] != 'missing' else []
    unknown = sorted(set(receiver_markets) - set(authorized))
    domain = sorted(m for m in receiver_markets if m in set(authorized))

    if not any(c['language'] == 'source' for c in cards):
        result = {'decision': 'not_required', 'reason': 'no_source_rule_cards', 'witnesses': []}
    elif source['decision'] != 'trusted':
        result = {'decision': 'blocked', 'reason': 'source_not_trusted', 'witnesses': []}
    elif translation.decide(db, approval_no, edition_no, language, as_of)['decision'] != 'released':
        result = {'decision': 'blocked', 'reason': 'language_not_released', 'witnesses': []}
    elif unknown:
        result = {'decision': 'blocked', 'reason': 'market_not_authorized',
                  'witnesses': [{'type': 'unauthorized_market', 'markets': unknown}]}
    else:
        section_hashes = json.loads(source['sections'])
        originals = [c for c in cards if c['language'] == 'source']
        if any(section_hashes.get(c['section_id']) != c['source_hash'] for c in originals):
            result = {'decision': 'blocked', 'reason': 'source_section_hash_mismatch',
                      'witnesses': []}
        else:
            result = equivalence.compare(cards, exceptions, authorized,
                                         source['form'], domain)

    source_summary = [equivalence.card_summary(c) for c in cards if c['language'] == 'source']
    translated_summary = [equivalence.card_summary(c) for c in cards if c['language'] == language]
    digest = equivalence.input_hash(adjudicated_at, approval_no, edition_no, language,
                                    receiver_type, receiver_id, domain, cards, exceptions)
    entry = {
        'adjudicated_at': adjudicated_at, 'approval_no': approval_no,
        'edition': str(edition_no), 'language': language,
        'receiver_type': receiver_type, 'receiver_id': receiver_id,
        'domain': domain, 'source_summary': source_summary,
        'translated_summary': translated_summary,
        'decision': result['decision'], 'reason': result['reason'],
        'witnesses': result['witnesses'], 'input_hash': digest}
    db.execute(
        'INSERT OR IGNORE INTO rule_card_certification'
        '(adjudicated_at,approval_no,edition,language,receiver_type,receiver_id,'
        'domain_json,source_summary_json,translated_summary_json,decision,reason,'
        'witnesses_json,input_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (entry['adjudicated_at'], entry['approval_no'], entry['edition'],
         entry['language'], entry['receiver_type'], entry['receiver_id'],
         json.dumps(entry['domain'], ensure_ascii=False),
         json.dumps(entry['source_summary'], ensure_ascii=False, sort_keys=True),
         json.dumps(entry['translated_summary'], ensure_ascii=False, sort_keys=True),
         entry['decision'], entry['reason'],
         json.dumps(entry['witnesses'], ensure_ascii=False, sort_keys=True), digest))
    return entry


# Engine decisions are preserved in history; the current-value table keeps
# the historical aligned/blocked vocabulary consumed by package decisions.
_VERDICT_MAP = {'equivalent': 'aligned', 'not_required': 'not_required'}


def decide(db, approval_no, edition_no, language, as_of):
    source = edition.at(db, approval_no, edition_no, as_of)
    markets = json.loads(source['markets']) if source['decision'] != 'missing' else []
    entry = certify(db, approval_no, edition_no, language, as_of,
                    'edition', approval_no + ':' + str(edition_no) + ':' + language, markets)
    decision = _VERDICT_MAP.get(entry['decision'], 'blocked')
    db.execute(
        'INSERT INTO rule_card_verdict VALUES (?,?,?,?,?) ON CONFLICT(approval_no,edition,language) '
        'DO UPDATE SET decision=excluded.decision,reason=excluded.reason',
        (approval_no, str(edition_no), language, decision, entry['reason']))
    return dict(entry, verdict_decision=decision)


def history(db, as_of=None, receiver_type=None, receiver_id=None):
    sql = ('SELECT id,adjudicated_at,approval_no,edition,language,receiver_type,receiver_id,'
           'domain_json,source_summary_json,translated_summary_json,decision,reason,'
           'witnesses_json,input_hash FROM rule_card_certification WHERE 1=1')
    args = []
    if as_of is not None:
        sql += ' AND adjudicated_at<=?'
        args.append(as_of)
    if receiver_type is not None:
        sql += ' AND receiver_type=?'
        args.append(receiver_type)
    if receiver_id is not None:
        sql += ' AND receiver_id=?'
        args.append(receiver_id)
    sql += ' ORDER BY id'
    out = []
    for r in rows(db, sql, tuple(args)):
        out.append({'id': r['id'], 'adjudicated_at': r['adjudicated_at'],
                    'approval_no': r['approval_no'], 'edition': r['edition'],
                    'language': r['language'], 'receiver_type': r['receiver_type'],
                    'receiver_id': r['receiver_id'], 'domain': json.loads(r['domain_json']),
                    'source_summary': json.loads(r['source_summary_json']),
                    'translated_summary': json.loads(r['translated_summary_json']),
                    'decision': r['decision'], 'reason': r['reason'],
                    'witnesses': json.loads(r['witnesses_json']), 'input_hash': r['input_hash']})
    return out
