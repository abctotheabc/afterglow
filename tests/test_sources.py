import json
from pathlib import Path
import sys
import unittest
from datetime import date
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import sources as s

class SourcesTests(unittest.TestCase):
    def test_distance_excludes_far_theaters(self):
        self.assertIn('philly',s.nearby(39.95,-75.16))
        self.assertNotIn('nyc',s.nearby(39.95,-75.16))
        with self.assertRaises(s.SourceError):s.nearby(None,None)
    def test_duration_formats(self):
        for value in ['PT2H5M','2hr 5m',125,'125']:
            self.assertEqual(s.runtime(value),125)
        self.assertIsNone(s.runtime(None))
    def test_amc_cancellation_and_runtime(self):
        t={'name':'AMC Test','id':1,'location':{'latitude':39.95,'longitude':-75.16},'websiteUrl':'https://example.com'}
        show={'id':2,'movieName':'Film','showDateTimeLocal':'2026-11-20T19:00:00','runTime':120}
        row=s.parse_amc(show,t)[0]
        self.assertEqual((row['area'],row['chain'],row['runtime']),('philly','AMC',120))
        self.assertEqual(s.parse_amc({**show,'isCanceled':True},t),[])
    def test_amc_header_and_pagination(self):
        class Client:
            def __init__(self):self.calls=[]
            def json(self,url,headers):
                self.calls.append((url,headers));return {'count':2,'_embedded':{'theatres':[{'id':len(self.calls)}]}}
        client=Client();rows=s.amc_pages(client,'/v2/theatres','theatres','test-placeholder')
        self.assertEqual(len(rows),2)
        self.assertEqual(client.calls[0][1]['X-AMC-Vendor-Key'],'test-placeholder')
        self.assertIn('page-number=2',client.calls[1][0])
    def test_cinemark_actual_calendar_date(self):
        graph=[{'@type':'MovieTheater','@id':'t','name':'Cinemark Test','url':'https://example.com','geo':{'latitude':39.95,'longitude':-75.16}}, {'@type':'Movie','@id':'m','name':'Film','duration':'PT2H','dateCreated':'2026-11-20'}, {'@type':'ScreeningEvent','@id':'e','name':'Film 11:30 PM IMAX Screening','startDate':'2026-11-20T23:30:00-05:00','workPresented':{'@id':'m'}}]
        html='<script type="application/ld+json">'+json.dumps({'@graph':graph})+'</script>'
        rows,_,_=s.parse_cinemark(html,'2026-11-20')
        self.assertEqual((rows[0]['runtime'],rows[0]['time'],rows[0]['format']),(120,'23:30','IMAX'))
        with self.assertRaises(s.WrongDate):s.parse_cinemark(html,'2026-11-21')
    def test_atom_cookie_date_and_early_am(self):
        venue={'@type':'MovieTheater','name':'Regal Test','geo':{'latitude':39.95,'longitude':-75.16}}
        html='<script type="application/ld+json">'+json.dumps(venue)+'</script><div data-date-dropdown><a data-value=\'{"serverFormat":"2026-11-20","isSelected":true}\'></a></div><div class="showtime-panel"><h2 data-qa="ProductionHeader_Title">Film</h2><span data-qa="ProductionMetadata_Runtime">2hr 5m</span><a data-qa="Showtimes_Button">7:30 PM</a><a data-qa="Showtimes_Button">12:30 AM</a></div>'
        rows,_,_=s.parse_atom(html,'2026-11-20','https://example.com/123')
        self.assertEqual(len(rows),1)
        self.assertEqual((rows[0]['time'],rows[0]['chain'],rows[0]['source']),('19:30','Regal','Atom'))
        with self.assertRaises(s.WrongDate):s.parse_atom(html,'2026-11-21','https://example.com/123')
    def test_alamo_after_midnight_uses_actual_date(self):
        p={'data':{'presentations':[{'slug':'film','show':{'title':'Film','runtimeMinutes':110}}], 'market':[{'slug':'nyc','cinemas':[{'id':'c','name':'Brooklyn','latitude':40.69,'longitude':-73.98}]}], 'sessions':[{'cinemaId':'c','sessionId':'1','presentationSlug':'film','showTimeClt':'2026-11-21T00:30:00','businessDateClt':'2026-11-20','status':'ONSALE'}]}}
        self.assertEqual(s.parse_alamo(p,'nyc',date(2026,11,20),date(2026,11,20)),[])
        row=s.parse_alamo(p,'nyc',date(2026,11,21),date(2026,11,21))[0]
        self.assertEqual((row['date'],row['time'],row['runtime']),('2026-11-21','00:30',110))
    def test_auth_failure_does_not_abort_other_sources(self):
        class Client:
            def json(self,*args):raise s.Unauthorized('Rejected key')
        with patch.dict('os.environ',{'AMC_API_KEY':'placeholder'}):
            rows,cov=s.collect_amc(Client(),date(2026,11,20),date(2026,11,24))
        self.assertEqual(rows,[])
        self.assertTrue(all(v['status']=='unauthorized' for v in cov.values()))
    def test_regal_default_never_requests_blocked_site(self):
        with patch.dict('os.environ',{'REGAL_DIRECT':'0'}):
            rows,cov=s.collect_regal(None,date(2026,11,20),date(2026,11,24))
        self.assertEqual(rows,[])
        self.assertTrue(all(v['status']=='blocked' for v in cov.values()))

class CheckpointTests(unittest.TestCase):
    def test_interrupted_import_preserves_unprocessed_source(self):
        import tempfile
        import refresh
        old={'screenings':[{'area':'nyc','source':'Screen Slate','title':'Film','theater':'Cinema','date':'2026-11-20','time':'19:00'}]}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'listings.json';path.write_text(json.dumps(old))
            cov={a:s.status() for a in s.CENTERS}
            with patch.object(refresh,'DATA',path),patch.object(s,'collect_cinemark',return_value=([],cov)),patch.object(s,'collect_alamo',side_effect=RuntimeError('interrupted')):
                with self.assertRaises(RuntimeError):refresh.collect(date(2026,11,18),selected=['Cinemark','Alamo','Screen Slate'])
            self.assertEqual(json.loads(path.read_text())['screenings'],old['screenings'])

class AddressTests(unittest.TestCase):
    def test_regal_address_requires_unambiguous_match(self):
        html='<h1 data-qa="VenueHeader_Name">Regal Gallery Place - Movies &amp; Showtimes</h1><span data-qa="VenueHeader_Address">701 Seventh Street Northwest, Washington, DC view on google maps</span>'
        class Client:
            def json(self,url):
                assert '701+Seventh' in url
                return {'result':{'addressMatches':[{'coordinates':{'y':38.8985,'x':-77.0218}}]}}
        venue,loc=s.atom_venue(html,Client());self.assertEqual(venue['name'],'Regal Gallery Place');self.assertIn('olney',loc)
        class NoMatch:
            def json(self,url):return {'result':{'addressMatches':[]}}
        with self.assertRaises(s.SourceError):s.atom_venue(html,NoMatch())
