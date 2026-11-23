"""Read-only showtime collectors. No seat maps, accounts, bookings or bot bypasses."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from urllib.parse import urljoin, urlparse, urlencode
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from preferences import CONFIG, allowed, locations_for, source_urls

ROOT=Path(__file__).resolve().parents[1]
ET=ZoneInfo('America/New_York')
# Approximate preset centers; distances are straight-line miles, not driving miles.
CENTERS={'nyc':(40.7506,-73.9972),'philly':(39.9526,-75.1652),'miami':(25.7617,-80.1918),'olney':(39.1532,-77.0669)}
ALAMO_MARKETS={'nyc':['nyc'],'philly':[],'miami':[],'olney':[]}
NAMES=['Screen Slate','AMC','Alamo','Cinemark','Regal','Atom']

class SourceError(Exception):pass
class Blocked(SourceError):pass
class Unauthorized(SourceError):pass
class WrongDate(SourceError):pass

def miles(a,b):
    lat1,lon1,lat2,lon2=map(math.radians,(*a,*b))
    h=math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 3958.7613*2*math.asin(min(1,math.sqrt(h)))

def nearby(lat,lon):
    if lat is None or lon is None:raise SourceError('Theater coordinates unavailable; radius cannot be verified')
    p=(float(lat),float(lon))
    return {a:round(miles(c,p),2) for a,c in CENTERS.items() if miles(c,p)<=30}

def runtime(value):
    if isinstance(value,(int,float)):return int(value) if value>0 else None
    s=str(value or '').strip()
    if s.isdigit():return int(s) or None
    m=re.fullmatch(r'PT(?:(\d+)H)?(?:(\d+)M)?',s,re.I)
    if m:return int(m[1] or 0)*60+int(m[2] or 0) or None
    h=re.search(r'(\d+)\s*(?:hr|hour|h)',s,re.I);m=re.search(r'(\d+)\s*m',s,re.I)
    return (int(h[1])*60 if h else 0)+(int(m[1]) if m else 0) or None

def chain(name):
    n=name.casefold()
    for marker,label in [('amc','AMC'),('regal','Regal'),('cinemark','Cinemark'),('alamo','Alamo')]:
        if marker in n:return label
    return 'Independent / other'

class Client:
    def __init__(self):
        self.cache=ROOT/'.cache/http';self.cache.mkdir(parents=True,exist_ok=True)
        self.blocked=set();self.lock=threading.Lock()
    def get(self,url,headers=None):
        headers=headers or {};host=urlparse(url).hostname
        if host in self.blocked:raise Blocked('Source blocked automated access; no bypass attempted')
        # The cache key is a one-way digest; credentials are never written to disk.
        p=self.cache/(sha256((url+json.dumps(headers,sort_keys=True)).encode()).hexdigest()+'.txt')
        if p.exists() and time.time()-p.stat().st_mtime<6*3600:return p.read_text(encoding='utf-8')
        config='\n'.join('header = '+json.dumps(k+': '+v) for k,v in headers.items())
        r=subprocess.run(['curl','--silent','--show-error','--location','--max-time','25','--user-agent','AfterglowPersonalCinema/2.0','--config','-','--write-out','\n%{http_code}',url],input=config,text=True,encoding='utf-8',capture_output=True)
        body,_,status=r.stdout.rpartition('\n')
        if 'Attention Required! | Cloudflare' in body or 'Sorry, you have been blocked' in body or 'cf-chl-' in body:
            with self.lock:self.blocked.add(host)
            raise Blocked('Source blocked automated access; no bypass attempted')
        if 'Unauthorized VendorKey' in body:raise Unauthorized('AMC rejected the vendor key (12005); activation or permissions required')
        if r.returncode or not status.startswith('2'):raise SourceError('Source returned HTTP '+status if status else 'Source request timed out')
        p.write_text(body,encoding='utf-8')
        return body
    def json(self,url,headers=None):
        try:return json.loads(self.get(url,headers))
        except json.JSONDecodeError:raise SourceError('Expected JSON; source format changed')

def parallel(fn,items,workers=3):
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(fn,x):x for x in items}
        for future in as_completed(futures):
            item=futures[future]
            try:yield item,future.result(),None
            except Exception as e:yield item,None,e

def status(state='ok',message='',**kw):
    return {'status':state,'message':message,'checkedAt':datetime.now(ET).isoformat(),**kw}

def venue_failure(coverage,field,url,day,error):
    for area,preset in CONFIG['areas'].items():
        if any(v.get(field)==url for v in preset['theaters']):
            coverage[area]['failures'].append({'url':url,'date':day,'error':str(error)})
            coverage[area]['failedDates'].append(day)

def dates(start,end):
    return [(start+timedelta(days=i)).isoformat() for i in range((end-start).days+1)]

def live_dates(start,end):
    # These commercial websites do not provide reliable historical showtimes.
    today=datetime.now(ET).date().isoformat()
    return [d for d in dates(start,end) if d>=today]

def row(source,title,theater,stamp,url,**extra):
    if not re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}',stamp or ''):raise SourceError('Invalid screening timestamp')
    return {'source':source,'title':title,'theater':theater,'chain':chain(theater),'date':stamp[:10],'time':stamp[11:16],'url':url,'runtime':None,'year':None,'poster':None,'format':'','note':'','fetchedAt':datetime.now(ET).isoformat(),**extra}

def distribute(base,locations):
    return [{**base,'area':a,'distance':d} for a,d in locations.items()]

def flight_text(html):
    result=[]
    for m in re.finditer(r'self\.__next_f\.push\((\[1,.*?\])\)</script>',html,re.S):
        try:
            a=json.loads(m[1]);result.append(a[1])
        except (ValueError,IndexError):continue
    return ''.join(result)

def structured_graph(html):
    soup=BeautifulSoup(html,'html.parser');docs=[]
    for tag in soup.select('script[type="application/ld+json"]'):
        try:docs.append(json.loads(tag.get_text()))
        except ValueError:continue
    flight=flight_text(html)
    for m in re.finditer(r'\{"@context"',flight):
        try:docs.append(json.JSONDecoder().raw_decode(flight[m.start():])[0])
        except ValueError:continue
    graph=[]
    for d in docs:
        if isinstance(d,dict):graph.extend(d.get('@graph',[d]))
    return graph

def parse_cinemark(html,requested):
    graph=structured_graph(html);theater=next((n for n in graph if n.get('@type')=='MovieTheater'),None)
    if not theater:raise SourceError('Cinemark theater metadata missing')
    geo=theater.get('geo',{});locations=nearby(geo.get('latitude'),geo.get('longitude'))
    films={n['@id']:n for n in graph if n.get('@type')=='Movie'};events=[n for n in graph if n.get('@type')=='ScreeningEvent']
    if events and not any(e.get('startDate','')[:10]==requested for e in events):raise WrongDate('Cinemark returned another date')
    rows=[]
    for e in events:
        stamp=e.get('startDate','')
        if stamp[:10]!=requested or e.get('eventStatus','').endswith('EventCancelled'):continue
        m=films.get((e.get('workPresented') or {}).get('@id'))
        if not m:raise SourceError('Cinemark screening has no movie metadata')
        formats=e['name'].removeprefix(m['name']).strip()
        formats=re.sub(r'^\d{1,2}:\d{2}\s*[AP]M\s*','',formats);formats=re.sub(r'\s*Screening$','',formats)
        b=row('Cinemark',m['name'],theater['name'],stamp,(e.get('offers') or {}).get('url') or theater['url'],runtime=runtime(m.get('duration') or e.get('duration')),year=int(m['dateCreated'][:4]) if m.get('dateCreated') else None,poster=m.get('image'),format=formats,theaterId=str(theater['@id']),id=e.get('@id') or (e.get('offers') or {}).get('url'),latitude=geo.get('latitude'),longitude=geo.get('longitude'))
        rows.extend(distribute(b,locations))
    return rows,locations,theater

def collect_cinemark(client,start,end):
    rows=[];coverage={a:status(theaters=0,failedDates=[],failures=[]) for a in CENTERS};days=live_dates(start,end)
    urls=source_urls('cinemarkUrl')
    if not days:return [],{a:status('not_available','Historical dates are not available') for a in CENTERS}
    venues=[];directory_errors=0
    def first(url):return parse_cinemark(client.get(url+'?showDate='+days[0]),days[0])
    for url,result,error in parallel(first,urls):
        if error:
            directory_errors+=1
            venue_failure(coverage,'cinemarkUrl',url,days[0],error)
            continue
        incoming,locations,t=result;locations=locations_for(t['name'],locations);rows.extend(incoming)
        if locations:
            venues.append((url,locations));
            for area in locations:coverage[area]['theaters']+=1
    def fetch(task):
        url,loc,d=task;return parse_cinemark(client.get(url+'?showDate='+d),d)[0]
    for task,incoming,error in parallel(fetch,[(u,loc,d) for u,loc in venues for d in days[1:]]):
        if error:
            for area in task[1]:coverage[area]['failedDates'].append(task[2]);coverage[area]['failures'].append({'url':task[0],'date':task[2],'error':str(error)})
        else:rows.extend(incoming)
    for a,c in coverage.items():
        c['failedDates']=sorted(set(c['failedDates']));c['directoryErrors']=directory_errors
        if c['failures']:c['status']='partial';c['message']='Some selected theater pages could not be read; coverage is incomplete.'
        elif not c['theaters']:c['status']='no_nearby';c['message']='No Cinemark theaters selected in this area.'
    print('Cinemark:',len(rows),'screenings',flush=True)
    return rows,coverage

def atom_venue(html,client=None):
    graph=structured_graph(html);v=next((n for n in graph if n.get('@type')=='MovieTheater'),None)
    if not v and client:
        soup=BeautifulSoup(html,'html.parser');name=soup.select_one('[data-qa="VenueHeader_Name"]');address=soup.select_one('[data-qa="VenueHeader_Address"]')
        # Regal and Angelika pages omit JSON-LD coordinates. Resolve their published street
        # address through the Census geocoder; never infer a radius from city alone.
        if name and address and (name.get_text(strip=True).startswith('Regal ') or name.get_text(strip=True).startswith('Angelika Film Center New York')):
            street=address.get_text(' ',strip=True).removesuffix('view on google maps').strip()
            result=client.json('https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?'+urlencode({'address':street,'benchmark':'Public_AR_Current','format':'json'}))
            matches=result.get('result',{}).get('addressMatches',[])
            if len(matches)==1:
                geo=matches[0]['coordinates'];v={'name':name.get_text(' ',strip=True).removesuffix(' - Movies & Showtimes'),'geo':{'latitude':geo['y'],'longitude':geo['x']}}
    if not v:raise SourceError('Atom theater metadata missing or address could not be resolved')
    geo=v.get('geo',{});return v,nearby(geo.get('latitude'),geo.get('longitude'))

def parse_atom(html,requested,url,client=None):
    soup=BeautifulSoup(html,'html.parser');v,locations=atom_venue(html,client)
    selected=[]
    for a in soup.select('[data-date-dropdown] [data-value]'):
        value=json.loads(a['data-value'])
        if value.get('isSelected'):selected.append(value.get('serverFormat'))
    if selected!=[requested]:raise WrongDate('Atom returned another date; ignored rather than relabeling showtimes')
    out=[]
    for panel in soup.select('.showtime-panel'):
        title=panel.select_one('[data-qa="ProductionHeader_Title"]');duration=panel.select_one('[data-qa="ProductionMetadata_Runtime"]');img=panel.select_one('img.poster')
        if not title:continue
        name=title.get_text(' ',strip=True);poster=(img.get('data-src') or img.get('src')) if img else None
        if poster and poster.startswith('https://atom-tickets-res.cloudinary.com/'):
            poster=poster.replace('h_126','h_600').replace('w_84','w_400')
        for b in panel.select('[data-qa="Showtimes_Button"]'):
            text=b.get_text(' ',strip=True);mt=re.search(r'\b(\d{1,2}):(\d{2})\s*(AM|PM)\b',text,re.I)
            if not mt:continue
            h=int(mt[1])%12+(12 if mt[3].upper()=='PM' else 0);stamp=requested+f'T{h:02}:{mt[2]}:00'
            metadata=json.loads(b.get('data-product-metadata','{}'))
            group=b.find_parent(attrs={'data-qa':'Showtimes_Wrapper'});attr=group.select_one('[data-qa="Showtimes_PrimaryAttribute"]') if group else None
            fmt=metadata.get('variant') or ((attr.get('alt') or attr.get_text(' ',strip=True)) if attr else '')
            b_row=row('Atom',name,v['name'],stamp,url,runtime=runtime(duration.get_text() if duration else None),year=int(metadata['releaseDate'][:4]) if metadata.get('releaseDate') else None,poster=poster,format=fmt,theaterId=url.rsplit('/',1)[-1],id=metadata.get('id'),latitude=v['geo']['latitude'],longitude=v['geo']['longitude'],note='Booking unavailable; confirm with theater.' if b.has_attr('disabled') else '')
            # Atom uses a business-day selector. Early AM screenings belong to the following civil date.
            # Its full timestamp is absent from the buttons, so omit ambiguous early-AM times.
            if h<6:continue
            out.extend(distribute(b_row,locations))
    return out,locations,v

def collect_atom(client,start,end):
    days=live_dates(start,end);rows=[];cov={a:status(theaters=0,failedDates=[],failures=[]) for a in CENTERS}
    if not days:return [],{a:status('not_available','Historical dates are not available') for a in CENTERS}
    # Use verified selected venue URLs; do not crawl every city directory.
    urls=source_urls('atomUrl')
    venues=[];discovery_errors=0
    def first(url):
        html=client.get(url,{'Cookie':'date='+days[0]})
        try:return (*parse_atom(html,days[0],url,client),None)
        except WrongDate as error:
            # A venue may publish future dates without publishing today's shows.
            v,locations=atom_venue(html,client)
            return [],locations,v,error
    for i,(url,result,error) in enumerate(parallel(first,sorted(urls))):
        if error:
            discovery_errors+=1
            venue_failure(cov,'atomUrl',url,days[0],error)
            continue
        incoming,locations,v,date_error=result;locations=locations_for(v['name'],locations);rows.extend(incoming)
        if date_error:venue_failure(cov,'atomUrl',url,days[0],date_error)
        if locations:
            venues.append((url,locations))
            for a in locations:cov[a]['theaters']+=1
        if (i+1)%30==0:print('Atom: inspected',i+1,'theater pages',flush=True)
    def fetch(task):
        u,loc,d=task;return parse_atom(client.get(u,{'Cookie':'date='+d}),d,u,client)[0]
    jobs=[(u,loc,d) for u,loc in venues for d in days[1:]]
    for i,(task,incoming,error) in enumerate(parallel(fetch,jobs)):
        if error:
            for a in task[1]:cov[a]['failedDates'].append(task[2]);cov[a]['failures'].append({'url':task[0],'date':task[2],'error':str(error)})
        else:rows.extend(incoming)
        if (i+1)%60==0:print('Atom: collected',i+1,'of',len(jobs),'remaining theater/date pages',flush=True)
    for a,c in cov.items():
        c['discoveryErrors']=discovery_errors;c['failedDates']=sorted(set(c['failedDates']))
        c['message']='Only selected theater pages are collected. Early-AM times omitted where calendar date is ambiguous.'
        if c['failures']:c['status']='partial'
        if not c['theaters']:c['status']='error';c['message']='No nearby Atom theater pages could be verified.'
    print('Atom:',len(rows),'screenings',flush=True)
    return rows,cov

def parse_alamo(payload,area,start,end,details=None):
    d=payload['data'];movies={p['slug']:p for p in d['presentations']};cinemas={c['id']:c for m in d['market'] for c in m['cinemas']};out=[]
    for s in d['sessions']:
        stamp=s['showTimeClt']
        if not start.isoformat()<=stamp[:10]<=end.isoformat() or s.get('isHidden') or s.get('status') not in ['ONSALE','SOLDOUT']:continue
        c=cinemas[s['cinemaId']];loc=nearby(c.get('latitude'),c.get('longitude'))
        if area not in loc or not allowed(area,'Alamo Drafthouse '+c['name']):continue
        p=(details or {}).get(s['presentationSlug']) or movies[s['presentationSlug']];show=p.get('show') or {};event=p.get('event') or {};m={**show,**{k:v for k,v in event.items() if v is not None}}
        title=BeautifulSoup(m.get('title') or s['presentationSlug'],'html.parser').get_text(' ',strip=True);release=m.get('nationalReleaseDateUtc');posters=show.get('posterImages') or []
        market=next(x['slug'] for x in d['market'] if c in x['cinemas'])
        url='https://drafthouse.com/'+market+'/show/'+s['presentationSlug']
        b=row('Alamo',title,'Alamo Drafthouse '+c['name'],stamp,url,id=s['cinemaId']+'-'+s['sessionId'],theaterId=c['id'],runtime=runtime(m.get('runtimeMinutes')),year=int(release[:4]) if release else None,poster=(m.get('posterImage') or {}).get('uri') or (posters[0].get('uri') if posters else None),format=s.get('formatSlug',''),latitude=c['latitude'],longitude=c['longitude'],note=('Special presentation; event timing may vary. ' if event else '')+('Sold out at last refresh.' if s['status']=='SOLDOUT' else ''))
        out.extend(distribute(b,{area:loc[area]}))
    return out

def collect_alamo(client,start,end):
    rows=[];cov={};payloads=[];jobs={}
    for area,markets in ALAMO_MARKETS.items():
        if not markets:cov[area]=status('no_nearby','No Alamo theaters selected in this area.');continue
        cov[area]=status(theaters=0,failures=[])
        for market in markets:
            try:
                p=client.json('https://drafthouse.com/s/mother/v2/schedule/market/'+market);payloads.append((area,market,p))
                cinemas={c['id']:c for m in p['data']['market'] for c in m['cinemas']}
                near={cid for cid,c in cinemas.items() if area in nearby(c.get('latitude'),c.get('longitude')) and allowed(area,'Alamo Drafthouse '+c['name'])};cov[area]['theaters']+=len(near)
                for s in p['data']['sessions']:
                    if s['cinemaId'] in near and start.isoformat()<=s['showTimeClt'][:10]<=end.isoformat():jobs.setdefault(s['presentationSlug'],market)
            except Exception as e:cov[area]['status']='partial';cov[area]['failures'].append(str(e))
    details={}
    def fetch(item):
        slug,market=item;return client.json('https://drafthouse.com/s/mother/v2/schedule/presentation/'+market+'/'+slug)['data']['presentation']
    for (slug,market),p,error in parallel(fetch,list(jobs.items())):
        if not error:details[slug]=p
    for area,market,p in payloads:rows.extend(parse_alamo(p,area,start,end,details))
    print('Alamo:',len(rows),'screenings',flush=True)
    return rows,cov

def amc_page(client,path,key):
    return client.json('https://api.amctheatres.com'+path,{'X-AMC-Vendor-Key':key,'Accept':'application/json'})

def amc_pages(client,path,kind,key):
    out=[];page=1
    while True:
        d=amc_page(client,path+('&' if '?' in path else '?')+urlencode({'page-size':100,'page-number':page}),key)
        if d.get('errors'):raise SourceError('AMC returned an API error')
        items=d['_embedded'][kind];out.extend(items)
        if len(out)>=d['count']:return out
        if not items:raise SourceError('AMC pagination stopped before total count')
        page+=1

def parse_amc(show,theater):
    if show.get('isCanceled'):return []
    loc=theater.get('location') or {};locations=nearby(loc.get('latitude'),loc.get('longitude'))
    b=row('AMC',show['movieName'],theater['name'],show['showDateTimeLocal'],show.get('purchaseUrl') or theater.get('websiteUrl'),id=str(show['id']),theaterId=str(theater['id']),runtime=runtime(show.get('runTime')),format=show.get('premiumFormat') or '',poster=(show.get('media') or {}).get('posterDynamic'),latitude=loc['latitude'],longitude=loc['longitude'],note='Sold out at last refresh.' if show.get('isSoldOut') else '')
    return distribute(b,locations)

def collect_amc(client,start,end):
    key=os.getenv('AMC_API_KEY')
    if not key:return [],{a:status('not_configured','Set AMC_API_KEY to enable the direct AMC API.') for a in CENTERS}
    try:theaters=amc_pages(client,'/v2/theatres','theatres',key)
    except Exception as e:return [],{a:status('unauthorized' if isinstance(e,Unauthorized) else 'error',str(e)) for a in CENTERS}
    nearby_theaters=[];rows=[];cov={a:status(theaters=0,failedDates=[]) for a in CENTERS}
    for t in theaters:
        loc=t.get('location') or {}
        try:locations=locations_for(t['name'],nearby(loc.get('latitude'),loc.get('longitude')))
        except SourceError:continue
        if locations:
            nearby_theaters.append((t,locations))
            for a in locations:cov[a]['theaters']+=1
    def fetch(task):
        t,loc,d=task;items=amc_pages(client,f'/v2/theatres/{t["id"]}/showtimes/{d}','showtimes',key)
        return [r for s in items for r in parse_amc(s,t) if r['date']==d]
    for task,incoming,error in parallel(fetch,[(t,loc,d) for t,loc in nearby_theaters for d in live_dates(start,end)]):
        if error:
            for a in task[1]:cov[a]['status']='partial';cov[a]['failedDates'].append(task[2])
        else:rows.extend(incoming)
    return rows,cov

def parse_regal(html,requested,venue):
    """Parser for documented public __NEXT_DATA__ format; direct access was blocked during verification."""
    soup=BeautifulSoup(html,'html.parser');tag=soup.select_one('script#__NEXT_DATA__')
    if not tag:raise SourceError('Regal structured showtimes missing')
    p=json.loads(tag.get_text())['props']['pageProps'];groups=p.get('showtimes')
    if not isinstance(groups,list):raise SourceError('Regal showtime schema changed')
    locations=nearby(venue['latitude'],venue['longitude']);rows=[]
    for g in groups:
        for m in g.get('Film',[]):
            for s in m.get('Performances',[]):
                stamp=s.get('CalendarShowTime','')
                if stamp[:10]!=requested:continue
                b=row('Regal',m['Title'],venue['name'],stamp,venue['url'],id=str(s.get('PerformanceId')),theaterId=venue['id'],runtime=runtime(m.get('RunTime')),format=', '.join(str(x) for x in s.get('PerformanceAttributes',[])),latitude=venue['latitude'],longitude=venue['longitude'])
                rows.extend(distribute(b,locations))
    return rows

def collect_regal(client,start,end):
    # Do not retry a known site block automatically. An explicit future opt-in can
    # test normal public pages from another deployment without any bypass behavior.
    if os.getenv('REGAL_DIRECT')!='1':
        return [],{a:status('blocked','Direct Regal access was blocked during verification. Regal theater listings are supplied by Atom where available.') for a in CENTERS}
    config=ROOT/'config/regal-venues.json'
    if not config.exists():return [],{a:status('not_configured','Supply verified Regal venue URLs and coordinates in config/regal-venues.json.') for a in CENTERS}
    rows=[];cov={a:status(theaters=0,failedDates=[]) for a in CENTERS}
    for venue in json.loads(config.read_text(encoding='utf-8')):
        loc=locations_for(venue['name'],nearby(venue['latitude'],venue['longitude']))
        if not loc:continue
        try:
            html=client.get(venue['url'])
            for d in live_dates(start,end):rows.extend(parse_regal(html,d,venue))
            for a in loc:cov[a]['theaters']+=1
        except Exception as e:
            for a in loc:cov[a]=status('blocked' if isinstance(e,Blocked) else 'error',str(e))
            if isinstance(e,Blocked):break
    return rows,cov
