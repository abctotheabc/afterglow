"""Weekly listings collector. Python 3.11+, curl and requirements.txt."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from html import unescape
import json
import os
from pathlib import Path
import re
import subprocess
import unicodedata
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'public/data/listings.json'
EASTERN = ZoneInfo('America/New_York')
AREAS = {'nyc': '10001', 'philly': '19103', 'miami': '33130', 'olney': '20832'}

def get(url, headers=None, timeout=25):
    # Feed headers through stdin so credentials do not appear in process arguments.
    config = '\n'.join('header = '+json.dumps(k+': '+v) for k,v in (headers or {}).items())
    p = subprocess.run(['curl','--fail','--silent','--show-error','--location','--max-time',str(timeout),'--retry','0' if timeout<25 else '1','--user-agent','AfterglowPersonalCinema/1.0','--config','-',url], input=config, text=True, capture_output=True)
    if p.returncode:
        raise RuntimeError('Source request failed (curl exit '+str(p.returncode)+')')
    return p.stdout

def clean(s):
    return ' '.join(unescape(re.sub('<[^>]+>', ' ', s or '')).split())

def normalize(s):
    return re.sub(r'[^a-z0-9]', '', unicodedata.normalize('NFKD', s).encode('ascii','ignore').decode().lower())

def parse_screenslate(html, requested_date):
    match = re.search(r'<script[^>]*class="listings-preload"[^>]*>(.*?)</script>', html, re.S)
    if not match:
        raise ValueError('Screen Slate page structure changed: preload not found')
    payload = json.loads(match.group(1))
    if payload.get('date') != requested_date or not isinstance(payload.get('screenings'), list) or not isinstance(payload.get('screeningsByDate'), list):
        raise ValueError('Unexpected Screen Slate date/schema')
    movies = {str(m['nid']): m for m in payload['screenings']}
    result=[]
    for show in payload['screeningsByDate']:
        m=movies.get(str(show['nid']))
        if not m: raise ValueError('Screening has no movie record')
        stamp=show.get('field_timestamp','')
        if not re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}',stamp): raise ValueError('Invalid screening timestamp')
        if stamp[:10]!=requested_date: continue
        info=clean(m.get('media_title_info'))
        runtimes=re.findall(r'\b(\d+)\s*M\b',info,re.I)
        years=re.findall(r'\b(?:18|19|20)\d{2}\b',info)
        # Do not mistake one film's runtime for a multi-film program's duration.
        multiple=len(runtimes)!=1 or ',' in str(m.get('media_title_ids',''))
        runtime=int(runtimes[0]) if runtimes and not multiple else None
        title=(clean(m.get('media_title_labels')) if not multiple else '') or clean(m.get('field_display_title')) or clean(m['title'])
        note=clean(show.get('field_note'))
        if multiple: note=(note+' · ' if note else '')+'Program runtime unconfirmed; end estimate unavailable.'
        result.append({'id':'ss-'+str(m['nid'])+'-'+stamp,'area':'nyc','source':'Screen Slate','title':title,'year':int(years[0]) if len(years)==1 else None,'runtime':runtime,'director':info.split(years[0])[0].strip() if years else '', 'format':clean(m.get('media_title_format')),'date':stamp[:10],'time':stamp[11:16],'theater':clean(m['venue_title']),'url':m.get('field_url') or 'https://www.screenslate.com/listings/'+requested_date,'note':note,'poster':None})
    return result

def load_local_env():
    """Optional local .env; never included in public output or ZIP."""
    path=ROOT/'.env'
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                k,v=line.split('=',1)
                if k.strip() in {'AMC_API_KEY','REGAL_DIRECT'}:os.environ.setdefault(k.strip(),v.strip().strip('"').strip("'"))


def collect(start, wikipedia=False, selected=None):
    from sources import Client, CENTERS, NAMES, collect_amc, collect_alamo, collect_cinemark, collect_atom, collect_regal, status, dates, chain
    end=start+timedelta(days=6);client=Client()
    old=json.loads(DATA.read_text()) if DATA.exists() else {'screenings':[]}
    selected=selected or NAMES
    rows=[s for s in old['screenings'] if start.isoformat()<=s['date']<=end.isoformat()]
    coverage={a:dict(old.get('coverage',{}).get(a,{})) for a in CENTERS}
    for c in coverage.values():
        c.pop('screenSlate',None);c.pop('fandango',None)
    all_days=dates(start,end)
    def save():
        current=list({(s['area'],s['source'],s['title'],s['theater'],s['date'],s['time'],s.get('format') or ''):s for s in rows}.values())
        result={'version':2,'updatedAt':datetime.now(EASTERN).isoformat(),'weekStart':start.isoformat(),'weekEnd':end.isoformat(),'coverage':coverage,'screenings':current}
        DATA.parent.mkdir(parents=True,exist_ok=True);temp=DATA.with_suffix('.tmp');temp.write_text(json.dumps(result,ensure_ascii=False,indent=2));temp.replace(DATA)
    for name in selected:
        if name=='Screen Slate':
            incoming=[];failed=[]
            for d in all_days:
                try:
                    part=parse_screenslate(client.get('https://www.screenslate.com/listings/'+d),d)
                    for s in part:s.update(chain=chain(s['theater']),fetchedAt=datetime.now(EASTERN).isoformat())
                    incoming.extend(part)
                except Exception:failed.append(d)
            cov={a:status('not_applicable','Screen Slate covers NYC in this app.') for a in CENTERS}
            cov['nyc']=status('error' if len(failed)==7 else 'partial' if failed else 'ok',failedDates=failed)
        else:
            fn={'AMC':collect_amc,'Alamo':collect_alamo,'Cinemark':collect_cinemark,'Atom':collect_atom,'Regal':collect_regal}[name]
            incoming,cov=fn(client,start,end)
        # Preserve unprocessed providers if a later source crashes or the run is interrupted.
        rows=[s for s in rows if s['source']!=name]
        for area,c in cov.items():
            c['availableFrom']=max(start.isoformat(),datetime.now(EASTERN).date().isoformat()) if name in ['Atom','Cinemark','AMC','Regal'] else start.isoformat()
            coverage[area][name]=c
            if c['status'] in ['partial','error','blocked','unauthorized','not_configured']:
                rows.extend({**s,'stale':True} for s in old['screenings'] if s['source']==name and s['area']==area and start.isoformat()<=s['date']<=end.isoformat())
        rows.extend(incoming);save()
        print(name+': '+str(len(incoming))+' fresh screenings; checkpoint saved',flush=True)
    # Fill artwork only on an exact normalized title + release-year match. Preserve
    # source runtimes (different cuts can have different lengths).
    art={}
    for s in [*old['screenings'],*rows]:
        if s.get('poster') and s.get('year'):art[(normalize(s['title']),s['year'])]={k:s[k] for k in ['poster','posterSource','posterPage'] if s.get(k)}
    for s in rows:
        if not s.get('poster'):
            s.update(art.get((normalize(s['title']),s.get('year')),{}))
    save()
    print('Finished. Coverage status:', {a:{n:c['status'] for n,c in v.items()} for a,v in coverage.items()},flush=True)
    return any(c['status'] in ['partial','error','blocked','unauthorized'] for v in coverage.values() for c in v.values())

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--start',type=date.fromisoformat);parser.add_argument('--wikipedia',action='store_true',help='Retained for compatibility; existing artwork is reused.');parser.add_argument('--sources',help='Comma-separated source names');parser.add_argument('--amc-key-prompt',action='store_true');args=parser.parse_args()
    load_local_env()
    if args.amc_key_prompt:
        import getpass
        os.environ['AMC_API_KEY']=getpass.getpass('AMC API key (hidden): ')
    today=datetime.now(EASTERN).date();start=args.start or today-timedelta(days=(today.weekday()-2)%7)
    selected=[s.strip() for s in args.sources.split(',')] if args.sources else None
    from sources import NAMES
    if selected and set(selected)-set(NAMES):parser.error('Unknown source name')
    collect(start,args.wikipedia,selected)
