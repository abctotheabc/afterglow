import importlib.util
import json
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('refresh',Path(__file__).resolve().parents[1]/'scripts/refresh.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)

class ParserTests(unittest.TestCase):
    def html(self, info='<span>Director</span><span>2006</span><span>120M</span>', ids='123'):
        p={'date':'2026-11-20','screenings':[{'nid':'1','title':'The Host','media_title_info':info,'media_title_ids':ids,'venue_title':'<a>IFC &amp; Center</a>','field_url':'https://example.org/film'}],'screeningsByDate':[{'nid':'1','field_timestamp':'2026-11-20T19:00:00','field_note':'Q&amp;A'}]}
        return '<script type="application/json" class="listings-preload">'+json.dumps(p)+'</script>'
    def test_real_shape(self):
        s=r.parse_screenslate(self.html(),'2026-11-20')[0]
        self.assertEqual((s['runtime'],s['year'],s['time'],s['theater'],s['note']),(120,2006,'19:00','IFC & Center','Q&A'))
    def test_program_runtime_unknown(self):
        self.assertIsNone(r.parse_screenslate(self.html(ids='123,456'),'2026-11-20')[0]['runtime'])
    def test_malformed_fails_loudly(self):
        with self.assertRaises(ValueError):r.parse_screenslate('<html>blocked</html>','2026-11-20')
    def test_wrong_date_rejected(self):
        with self.assertRaises(ValueError):r.parse_screenslate(self.html(),'2026-11-21')
    def test_unknown_runtime(self):
        self.assertIsNone(r.parse_screenslate(self.html(info='Director 2026'),'2026-11-20')[0]['runtime'])
    def test_movie_label_wins_over_event_title(self):
        html=self.html().replace('"title": "The Host"','"title": "The Host at IFC Center", "media_title_labels": "<span>The Host</span>"')
        self.assertEqual(r.parse_screenslate(html,'2026-11-20')[0]['title'],'The Host')

if __name__=='__main__':unittest.main()
