import json
from . import edition,translation,package,rulecard
from .store import rows

def decide(db,as_of):
    with db:
        edition.adjudicate(db)
        languages=[]
        for r in rows(db,'SELECT DISTINCT approval_no,edition,language FROM section_translation'):
            languages.append(translation.decide(db,r['approval_no'],r['edition'],r['language'],as_of))
        packages=[]
        for r in rows(db,'SELECT DISTINCT sku FROM sku_evidence'):
            packages.append(package.decide(db,r['sku'],as_of))
        keys=rows(db,'SELECT DISTINCT d.approval_no,d.edition FROM edition_evidence d JOIN evidence e USING(event_id) WHERE e.event_time<=? ORDER BY d.approval_no,d.edition',(as_of,))
        return {'as_of':as_of,'editions':[edition.at(db,k['approval_no'],k['edition'],as_of) for k in keys],
                'languages':languages,'rule_cards':rows(db,'SELECT * FROM rule_card_verdict ORDER BY approval_no,edition,language'),'packages':packages,'replacement':rows(db,'SELECT * FROM replacement_plan ORDER BY priority DESC,lot'),
                'publication_receipts':rows(db,'SELECT * FROM publication_receipt ORDER BY id')}
