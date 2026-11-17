"""SKU equivalence, market/language release and stock replacement priority."""
import json,re
from .sources import record
from .store import rows
from . import edition,translation


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
            else:raise ValueError('unknown package op '+e['op'])
        for r in rows(db,'SELECT DISTINCT sku FROM sku_evidence'):
            decide(db,r['sku'],max(e['event_time'] for e in events))
    return {'imported':made,'bindings':rows(db,'SELECT * FROM label_binding'),'replacement':rows(db,'SELECT * FROM replacement_plan')}


def latest_sku(db,sku,as_of):
    row=db.execute('SELECT s.*,e.event_time FROM sku_evidence s JOIN evidence e USING(event_id) WHERE sku=? AND e.event_time<=? ORDER BY e.event_time DESC,s.event_id DESC LIMIT 1',(sku,as_of)).fetchone()
    return dict(row) if row else None


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
        decision='published' if language['decision']=='released' else 'frozen'
        reason='all_dependencies_valid' if decision=='published' else 'language_not_released'
        doc_hash=source['document_hash']
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
