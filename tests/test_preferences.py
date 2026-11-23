import json
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import preferences as p
import sources as s
import refresh

class PreferenceTests(unittest.TestCase):
    def test_exact_selection(self):
        self.assertEqual({a:len(v['theaters']) for a,v in p.CONFIG['areas'].items()}, {'nyc':35,'olney':11,'miami':4,'philly':25})
        self.assertTrue(p.allowed('nyc','AMC 19th St East 6'))
        self.assertTrue(p.allowed('nyc','Angelika New York'))
        self.assertFalse(p.allowed('nyc','AMC Staten Island Mall 11'))
        self.assertFalse(p.allowed('olney','Regal Gallery Place'))
        self.assertFalse(p.allowed('miami','AMC Hialeah 12'))
        self.assertTrue(p.allowed('philly','Regal UA King Of Prussia'))

    def test_atom_fetches_only_selected_urls_and_reaches_second_week(self):
        start=date(2026,11,25);days=s.dates(start,start+timedelta(days=13))
        calls=[]
        class Client:
            def get(self,url,headers):
                calls.append((url,headers['Cookie'][5:]))
                return url
        def parse(html,day,url,client):
            v=next(v for area in p.CONFIG['areas'].values() for v in area['theaters'] if v.get('atomUrl')==url)
            area=next(a for a,preset in p.CONFIG['areas'].items() if v in preset['theaters'])
            return [],{area:1},{'name':v['name']}
        with patch.object(s,'live_dates',return_value=days),patch.object(s,'parse_atom',side_effect=parse):
            s.collect_atom(Client(),start,start+timedelta(days=13))
        self.assertEqual(set(calls),{(url,day) for url in p.source_urls('atomUrl') for day in days})

    def test_cinemark_only_two_philadelphia_venues(self):
        urls=p.source_urls('cinemarkUrl')
        self.assertEqual(len(urls),2)
        self.assertTrue(any('somerdale' in u for u in urls))
        self.assertTrue(any('university-city' in u for u in urls))

    def test_failed_refresh_cannot_restore_removed_theaters(self):
        kept={'area':'nyc','source':'Atom','title':'Café','theater':'AMC Village 7','date':'2026-12-08','time':'19:00'}
        removed={**kept,'theater':'AMC Aviation 12'}
        outside={**kept,'date':'2026-12-09'}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'listings.json'
            path.write_text(json.dumps({'screenings':[kept,removed,outside]},ensure_ascii=False),encoding='utf-8')
            cov={a:s.status('error') for a in s.CENTERS}
            with patch.object(refresh,'DATA',path),patch.object(s,'collect_atom',return_value=([],cov)):
                refresh.collect(date(2026,11,25),selected=['Atom'])
            result=json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(result['weekEnd'],'2026-12-08')
        self.assertEqual(result['screenings'],[{**kept,'stale':True}])

    def test_angelika_link_and_address(self):
        html='<h1 data-qa="VenueHeader_Name">Angelika Film Center New York - Movies &amp; Showtimes</h1><span data-qa="VenueHeader_Address">18 West Houston Street, New York, NY view on google maps</span>'
        class Client:
            def json(self,url):return {'result':{'addressMatches':[{'coordinates':{'y':40.7258,'x':-73.9975}}]}}
        v,locations=s.atom_venue(html,Client())
        self.assertIn('nyc',locations)
        rows=p.selected_rows([{'area':'nyc','theater':v['name'],'url':'https://www.atomtickets.com/theaters/angelika-film-center-new-york/1'}])
        self.assertEqual(rows[0]['url'],'https://angelikafilmcenter.com/nyc/now-playing')

    def test_missing_first_date_does_not_skip_future_atom_dates(self):
        url=p.source_urls('atomUrl')[0];name='AMC Village 7'
        class Client:
            def get(self,*args):return 'html'
        with patch.object(s,'source_urls',return_value=[url]),patch.object(s,'live_dates',return_value=['2026-11-25','2026-11-26']),patch.object(s,'atom_venue',return_value=({'name':name},{'nyc':1})),patch.object(s,'parse_atom',side_effect=[s.WrongDate('unpublished'),([] ,{'nyc':1},{'name':name})]) as parse:
            s.collect_atom(Client(),date(2026,11,25),date(2026,12,8))
        self.assertEqual(parse.call_count,2)
