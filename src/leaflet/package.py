"""SKU equivalence, market/language release and stock replacement priority."""
import json,re
from .sources import record
from .store import rows
from . import edition,translation,rulecard


def strength_mg(value):
    match=re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(mg|g)\s*',value)
    if not match:raise ValueError('invalid strength unit')
    return round(float(match.group(1))*(1000 if match.group(2)=='g' else 1))


def ingest(db,events):
    made=0
    with db:
        for e in events:
            if e['op']=='sku':
                if record(db,e,'sku',e['sku']):
                    db.execute('INSERT INTO sku_evidence VALUES (?,?,?,?,?,?,?,?)',
                               (e['event_id'],e['sku'],e['approval_no'],str(e['edition']),e['form'],e['strength'],e['market'],e['language']));made+=1
            elif e['op']=='inventory':
                if record(db,e,'inventory',e['lot']):
                    db.execute('INSERT INTO inventory_label VALUES (?,?,?,?,?)',(e['event_id'],e['lot'],e['sku'],str(e['printed_edition']),e['units']));made+=1
            elif e['op']=='print-contract':
                if record(db,e,'print-contract',e['contract_id']):
                    db.execute('INSERT INTO print_contract_evidence VALUES (?,?,?,?,?,?,?)',
                               (e['event_id'],e['contract_id'],e['approval_no'],str(e['edition']),
                                e['language'],e['form'],json.dumps(sorted(e['markets']))));made+=1
            else:raise ValueError('unknown package op '+e['op'])
        for r in rows(db,'SELECT DISTINCT sku FROM sku_evidence'):
            decide(db,r['sku'],max(e['event_time'] for e in events))
        for r in rows(db,'SELECT DISTINCT contract_id FROM print_contract_evidence'):
            decide_contract(db,r['contract_id'],max(e['event_time'] for e in events))
    return {'imported':made,'bindings':rows(db,'SELECT * FROM label_binding'),
            'replacement':rows(db,'SELECT * FROM replacement_plan'),
            'contracts':rows(db,'SELECT * FROM print_contract_binding ORDER BY contract_id')}


def latest_sku(db,sku,as_of):
    row=db.execute('SELECT s.*,e.event_time FROM sku_evidence s JOIN evidence e USING(event_id) WHERE sku=? AND e.event_time<=? ORDER BY e.event_time DESC,s.event_id DESC LIMIT 1',(sku,as_of)).fetchone()
    return dict(row) if row else None


def latest_contract(db,contract_id,as_of):
    row=db.execute('SELECT c.*,e.event_time FROM print_contract_evidence c JOIN evidence e USING(event_id) WHERE contract_id=? AND e.event_time<=? ORDER BY e.event_time DESC,c.event_id DESC LIMIT 1',(contract_id,as_of)).fetchone()
    return dict(row) if row else None


# Certification decisions that let material leave the gate.
_PUBLISHABLE={'equivalent','not_required'}
_FROZEN_REASON={
    'different':'rule_cards_not_equivalent',
    'pending':'rule_cards_pending_evidence',
    'blocked':'rule_cards_blocked',
}


def decide(db,sku,as_of):
    s=latest_sku(db,sku,as_of)
    if not s:return {'sku':sku,'decision':'missing','reason':'no_sku'}
    source=edition.at(db,s['approval_no'],s['edition'],as_of)
    if source['decision']!='trusted':decision='frozen';reason='edition_not_trusted';doc_hash=None
    elif s['form']!=source['form'] or strength_mg(s['strength'])!=source['strength_mg']:
        decision='frozen';reason='form_or_strength_mismatch';doc_hash=source['document_hash']
    elif s['market'] not in json.loads(source['markets']):
        decision='frozen';reason='market_not_authorized';doc_hash=source['document_hash']
    else:
        language=translation.decide(db,s['approval_no'],s['edition'],s['language'],as_of)
        doc_hash=source['document_hash']
        if language['decision']!='released':
            decision='frozen';reason='language_not_released'
        else:
            # Electronic delivery for one SKU only needs equivalence on the
            # SKU's own market and form; the projection is stored under its
            # own receiver and never stands in for full-edition equivalence.
            cert=rulecard.certify(db,s['approval_no'],s['edition'],s['language'],as_of,
                                  'sku',sku,[s['market']])
            if cert['decision'] in _PUBLISHABLE:
                decision='published';reason='all_dependencies_valid'
            else:
                decision='frozen';reason=_FROZEN_REASON.get(cert['decision'],'rule_cards_not_equivalent')
    old=db.execute('SELECT decision,edition,document_hash FROM label_binding WHERE sku=?',(sku,)).fetchone()
    db.execute('INSERT INTO label_binding VALUES (?,?,?,?,?) ON CONFLICT(sku) DO UPDATE SET edition=excluded.edition,document_hash=excluded.document_hash,decision=excluded.decision,reason=excluded.reason',
               (sku,s['edition'],doc_hash,decision,reason))
    if not old or (old['decision'],old['edition'],old['document_hash'])!=(decision,s['edition'],doc_hash):
        db.execute('INSERT INTO publication_receipt(sku,decision,edition,document_hash) VALUES (?,?,?,?)',(sku,decision,s['edition'],doc_hash))
    for lot in rows(db,'SELECT * FROM inventory_label WHERE sku=?',(sku,)):
        old_source=edition.at(db,s['approval_no'],lot['printed_edition'],as_of)
        priority=100 if old_source['decision']=='withdrawn' else 50 if lot['printed_edition']!=s['edition'] else 0
        status='urgent' if priority==100 else 'scheduled' if priority==50 else 'current'
        db.execute('INSERT INTO replacement_plan VALUES (?,?,?,?) ON CONFLICT(lot) DO UPDATE SET priority=excluded.priority,decision=excluded.decision,reason=excluded.reason',
                   (lot['lot'],priority,status,'targeted_withdrawal' if priority==100 else 'newer_edition' if priority==50 else 'same_edition'))
    return {'sku':sku,'decision':decision,'reason':reason,'edition':s['edition'],'document_hash':doc_hash}


def decide_contract(db,contract_id,as_of):
    """Printed material riding with physical stock must cover the union of
    every market the packaging enters; a single-market pass cannot unfreeze it."""
    c=latest_contract(db,contract_id,as_of)
    if not c:return {'contract_id':contract_id,'decision':'missing','reason':'no_contract'}
    source=edition.at(db,c['approval_no'],c['edition'],as_of)
    markets=json.loads(c['markets_json'])
    if source['decision']!='trusted':
        decision,reason='frozen','edition_not_trusted'
    elif c['form']!=source['form']:
        decision,reason='frozen','form_or_strength_mismatch'
    elif any(m not in json.loads(source['markets']) for m in markets):
        decision,reason='frozen','market_not_authorized'
    else:
        language=translation.decide(db,c['approval_no'],c['edition'],c['language'],as_of)
        if language['decision']!='released':
            decision,reason='frozen','language_not_released'
        else:
            cert=rulecard.certify(db,c['approval_no'],c['edition'],c['language'],as_of,
                                  'print_contract',contract_id,markets)
            if cert['decision'] in _PUBLISHABLE:
                decision,reason='published','all_dependencies_valid'
            else:
                decision='frozen';reason=_FROZEN_REASON.get(cert['decision'],'rule_cards_not_equivalent')
    old=db.execute('SELECT decision,edition FROM print_contract_binding WHERE contract_id=?',(contract_id,)).fetchone()
    db.execute('INSERT INTO print_contract_binding VALUES (?,?,?,?) ON CONFLICT(contract_id) DO UPDATE SET edition=excluded.edition,decision=excluded.decision,reason=excluded.reason',
               (contract_id,c['edition'],decision,reason))
    if not old or (old['decision'],old['edition'])!=(decision,c['edition']):
        db.execute('INSERT INTO contract_publication_receipt(contract_id,decision,edition) VALUES (?,?,?)',
                   (contract_id,decision,c['edition']))
    return {'contract_id':contract_id,'edition':c['edition'],'decision':decision,'reason':reason,
            'markets':markets}
