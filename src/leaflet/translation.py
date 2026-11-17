"""Section coverage and independent translation sign-off."""
from .sources import record
from .store import rows
from . import edition
import json

REQUIRED={'scope','contraindication','storage'}


def ingest(db,events):
    made=0
    with db:
        for e in events:
            if e['op']=='section':
                if record(db,e,'translation',e['approval_no']):
                    db.execute('INSERT INTO section_translation VALUES (?,?,?,?,?,?,?,?)',
                               (e['event_id'],e['approval_no'],str(e['edition']),e['language'],e['section_id'],e['source_hash'],e['translated_text'],e['submitted_by']));made+=1
            elif e['op']=='signoff':
                if record(db,e,'signoff',e['approval_no']):
                    db.execute('INSERT INTO review_signature VALUES (?,?,?,?,?,?,?)',
                               (e['event_id'],e['approval_no'],str(e['edition']),e['language'],e['reviewer'],e['source_hash'],e['valid_until']));made+=1
            else:raise ValueError('unknown translation op '+e['op'])
        for r in rows(db,'SELECT DISTINCT approval_no,edition,language FROM section_translation'):
            decide(db,r['approval_no'],r['edition'],r['language'],max(e['event_time'] for e in events))
    return {'imported':made,'languages':rows(db,'SELECT * FROM language_release')}


def decide(db,approval_no,edition_no,language,as_of):
    source=edition.at(db,approval_no,edition_no,as_of)
    sections=rows(db,'SELECT t.*,e.event_time FROM section_translation t JOIN evidence e USING(event_id) WHERE t.approval_no=? AND t.edition=? AND t.language=? AND e.event_time<=? ORDER BY e.event_time DESC,t.event_id DESC',
                  (approval_no,str(edition_no),language,as_of))
    selected={}
    for s in sections:selected.setdefault(s['section_id'],s)
    signature=db.execute('SELECT * FROM review_signature WHERE approval_no=? AND edition=? AND language=? AND valid_until>=? ORDER BY valid_until DESC,event_id DESC LIMIT 1',
                         (approval_no,str(edition_no),language,as_of)).fetchone()
    if source['decision']!='trusted':decision='blocked';reason='source_not_trusted'
    else:
        originals=json.loads(source['sections'])
        if not REQUIRED.issubset(originals):decision='draft';reason='original_sections_missing'
        elif not REQUIRED.issubset(selected):decision='draft';reason='translated_sections_missing'
        elif any(selected[k]['source_hash']!=originals[k] for k in REQUIRED):decision='draft';reason='section_hash_mismatch'
        elif not signature:decision='draft';reason='no_current_signoff'
        elif signature['source_hash']!=source['document_hash']:decision='draft';reason='signoff_for_old_edition'
        elif signature['reviewer'] in {selected[k]['submitted_by'] for k in REQUIRED}:decision='draft';reason='self_signoff'
        else:decision='released';reason='complete_independent_signoff'
    db.execute('INSERT INTO language_release VALUES (?,?,?,?,?) ON CONFLICT(approval_no,edition,language) DO UPDATE SET decision=excluded.decision,reason=excluded.reason',
               (approval_no,str(edition_no),language,decision,reason))
    return {'approval_no':approval_no,'edition':str(edition_no),'language':language,'decision':decision,'reason':reason}
