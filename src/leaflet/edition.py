"""Issuer authority, predecessor chain and targeted withdrawal."""
import json
from .sources import record
from .store import rows


def ingest(db,events):
    made=0
    with db:
        for e in events:
            op=e['op']
            if op=='authority':
                if record(db,e,'authority',e['approval_no']):
                    db.execute('INSERT INTO publisher_authority VALUES (?,?,?,?,?)',(e['event_id'],e['publisher'],e['approval_no'],e['valid_from'],e['valid_until']));made+=1
            elif op=='edition':
                if record(db,e,'edition',e['approval_no']):
                    db.execute('INSERT INTO edition_evidence VALUES (?,?,?,?,?,?,?,?,?,?)',
                               (e['event_id'],e['approval_no'],str(e['edition']),e['publisher'],str(e.get('predecessor') or ''),e['document_hash'],e['form'],e['strength_mg'],json.dumps(e['markets']),json.dumps(e['sections'],sort_keys=True)));made+=1
            elif op=='withdraw':
                if record(db,e,'withdrawal',e['approval_no']):
                    db.execute('INSERT INTO edition_withdrawal VALUES (?,?,?)',(e['event_id'],e['approval_no'],str(e['edition'])));made+=1
            else:raise ValueError('unknown edition op '+op)
        adjudicate(db)
    return {'imported':made,'editions':rows(db,'SELECT * FROM edition_decision ORDER BY approval_no,edition')}


def adjudicate(db):
    keys=rows(db,'SELECT DISTINCT approval_no,edition FROM edition_evidence')
    # Multiple passes let a predecessor arrive after its successor while retaining
    # the successor's original event time and immutable source evidence.
    for key in keys * max(1,len(keys)):
        docs=rows(db,'SELECT d.*,e.event_time FROM edition_evidence d JOIN evidence e USING(event_id) WHERE approval_no=? AND edition=? ORDER BY e.event_time DESC,d.event_id DESC',(key['approval_no'],key['edition']))
        d=docs[0]
        if len({x['document_hash'] for x in docs})>1:decision='disputed';reason='same_edition_different_hash';doc_hash=None
        elif db.execute('SELECT 1 FROM edition_withdrawal WHERE approval_no=? AND edition=?',(key['approval_no'],key['edition'])).fetchone():
            decision='withdrawn';reason='targeted_withdrawal';doc_hash=d['document_hash']
        elif not db.execute('SELECT 1 FROM publisher_authority WHERE approval_no=? AND publisher=? AND valid_from<=? AND valid_until>=?',
                            (d['approval_no'],d['publisher'],d['event_time'],d['event_time'])).fetchone():
            decision='untrusted';reason='publisher_not_authorized';doc_hash=None
        elif d['predecessor'] and not db.execute("SELECT 1 FROM edition_decision WHERE approval_no=? AND edition=? AND decision IN ('trusted','withdrawn')",(d['approval_no'],d['predecessor'])).fetchone():
            decision='pending';reason='predecessor_missing_or_untrusted';doc_hash=None
        else:decision='trusted';reason='authority_and_lineage_verified';doc_hash=d['document_hash']
        db.execute('INSERT INTO edition_decision VALUES (?,?,?,?,?) ON CONFLICT(approval_no,edition) DO UPDATE SET document_hash=excluded.document_hash,decision=excluded.decision,reason=excluded.reason',
                   (key['approval_no'],key['edition'],doc_hash,decision,reason))


def get(db,approval_no,edition):
    row=db.execute('SELECT d.*,e.form,e.strength_mg,e.markets,e.sections FROM edition_decision d LEFT JOIN edition_evidence e ON e.approval_no=d.approval_no AND e.edition=d.edition AND e.document_hash=d.document_hash WHERE d.approval_no=? AND d.edition=? ORDER BY e.event_id DESC LIMIT 1',(approval_no,str(edition))).fetchone()
    return dict(row) if row else {'approval_no':approval_no,'edition':str(edition),'decision':'missing','reason':'no_source'}


def at(db,approval_no,edition_no,as_of,seen=None):
    """Compute an edition verdict from evidence visible at a historical instant."""
    edition_no=str(edition_no);seen=set() if seen is None else set(seen)
    key=(approval_no,edition_no)
    if key in seen:return {'approval_no':approval_no,'edition':edition_no,'decision':'pending','reason':'lineage_cycle'}
    seen.add(key)
    docs=rows(db,'SELECT d.*,e.event_time FROM edition_evidence d JOIN evidence e USING(event_id) WHERE d.approval_no=? AND d.edition=? AND e.event_time<=? ORDER BY e.event_time DESC,d.event_id DESC',
              (approval_no,edition_no,as_of))
    if not docs:return {'approval_no':approval_no,'edition':edition_no,'decision':'missing','reason':'no_source'}
    d=docs[0];result=dict(d)
    if len({x['document_hash'] for x in docs})>1:decision='disputed';reason='same_edition_different_hash'
    elif db.execute('SELECT 1 FROM edition_withdrawal w JOIN evidence e USING(event_id) WHERE w.approval_no=? AND w.edition=? AND e.event_time<=?',
                    (approval_no,edition_no,as_of)).fetchone():decision='withdrawn';reason='targeted_withdrawal'
    elif not db.execute('SELECT 1 FROM publisher_authority a JOIN evidence e USING(event_id) WHERE a.approval_no=? AND a.publisher=? AND e.event_time<=? AND a.valid_from<=? AND a.valid_until>=?',
                        (approval_no,d['publisher'],as_of,d['event_time'],d['event_time'])).fetchone():decision='untrusted';reason='publisher_not_authorized'
    elif d['predecessor'] and at(db,approval_no,d['predecessor'],as_of,seen)['decision'] not in ('trusted','withdrawn'):
        decision='pending';reason='predecessor_missing_or_untrusted'
    else:decision='trusted';reason='authority_and_lineage_verified'
    result.update(decision=decision,reason=reason)
    return result
