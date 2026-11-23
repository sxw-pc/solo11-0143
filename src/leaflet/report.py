from . import edition,translation,package,rulecard
from .store import rows

def decide(db,as_of):
    with db:
        edition.adjudicate(db)
        languages=[]
        for r in rows(db,'SELECT DISTINCT approval_no,edition,language FROM section_translation'):
            languages.append(translation.decide(db,r['approval_no'],r['edition'],r['language'],as_of))
        # Re-adjudicate every captured translated language at the query instant
        # so the current-value table reflects evidence visible then; immutable
        # certifications preserve every historical adjudication separately.
        rule_cards=[]
        for r in rows(db,'SELECT DISTINCT approval_no,edition,language FROM rule_card WHERE language<>"source"'):
            entry=rulecard.decide(db,r['approval_no'],r['edition'],r['language'],as_of)
            rule_cards.append({'approval_no':r['approval_no'],'edition':r['edition'],
                               'language':r['language'],'decision':entry['verdict_decision'],
                               'reason':entry['reason']})
        packages=[]
        for r in rows(db,'SELECT DISTINCT sku FROM sku_evidence'):
            packages.append(package.decide(db,r['sku'],as_of))
        contracts=[]
        for r in rows(db,'SELECT DISTINCT contract_id FROM print_contract_evidence'):
            contracts.append(package.decide_contract(db,r['contract_id'],as_of))
        keys=rows(db,'SELECT DISTINCT d.approval_no,d.edition FROM edition_evidence d JOIN evidence e USING(event_id) WHERE e.event_time<=? ORDER BY d.approval_no,d.edition',(as_of,))
        return {'as_of':as_of,'editions':[edition.at(db,k['approval_no'],k['edition'],as_of) for k in keys],
                'languages':languages,
                'rule_cards':sorted(rule_cards,key=lambda x:(x['approval_no'],x['edition'],x['language'])),
                'certifications':rulecard.history(db,as_of),
                'packages':packages,'contracts':contracts,
                'replacement':rows(db,'SELECT * FROM replacement_plan ORDER BY priority DESC,lot'),
                'publication_receipts':rows(db,'SELECT * FROM publication_receipt ORDER BY id'),
                'contract_receipts':rows(db,'SELECT * FROM contract_publication_receipt ORDER BY id')}
