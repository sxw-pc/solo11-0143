import argparse,json
from pathlib import Path
from . import edition,translation,package,report
from .store import connect

def main(argv=None):
    p=argparse.ArgumentParser(prog='leaflet');sp=p.add_subparsers(dest='command',required=True)
    for name in ('init','import-source','align','map-package','report','demo'):
        q=sp.add_parser(name);q.add_argument('--db',default='data/demo.sqlite')
        if name in ('import-source','align','map-package'):q.add_argument('--input',required=True)
        if name=='report':q.add_argument('--as-of',required=True)
    a=p.parse_args(argv);db=connect(a.db)
    try:
        if a.command=='init':result={'initialized':True,'db':a.db}
        elif a.command in ('import-source','align','map-package'):
            events=json.loads(Path(a.input).read_text(encoding='utf-8'))
            result={'import-source':edition,'align':translation,'map-package':package}[a.command].ingest(db,events)
        elif a.command=='report':result=report.decide(db,a.as_of)
        else:
            root=Path(__file__).resolve().parents[2]
            for name,mod in [('source_good.json',edition),('translation_good.json',translation),('package_good.json',package)]:
                mod.ingest(db,json.loads((root/'fixtures'/'leaflet'/name).read_text(encoding='utf-8')))
            result=report.decide(db,'2026-11-17T10:00:00Z')
        print(json.dumps(result,ensure_ascii=False,sort_keys=True));return 0
    except (KeyError,ValueError) as e:print(json.dumps({'error':str(e)},ensure_ascii=False));return 2
    finally:db.close()

if __name__=='__main__':raise SystemExit(main())
