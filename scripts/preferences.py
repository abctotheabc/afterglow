"""The selected venues are shared with the browser in public/data/theaters.json."""
import json
from pathlib import Path
import re
import unicodedata

CONFIG = json.loads((Path(__file__).resolve().parents[1] / 'public/data/theaters.json').read_text(encoding='utf-8'))
DAYS = CONFIG['days']

def key(name):
    return re.sub(r'[^a-z0-9]', '', unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower())

def venue(area, name):
    return next((v for v in CONFIG['areas'][area]['theaters'] if key(name) in {key(n) for n in [v['name'], *v.get('aliases', [])]}), None)

def allowed(area, name):
    return venue(area, name) is not None

def locations_for(name, locations):
    return {a: d for a, d in locations.items() if allowed(a, name)}

def source_urls(field):
    return sorted({v[field] for a in CONFIG['areas'].values() for v in a['theaters'] if v.get(field)})

def selected_rows(rows):
    result = []
    for row in rows:
        v = venue(row['area'], row['theater'])
        if v:
            result.append({**row, 'theater': v['name'], **({'url': v['listingUrl']} if v.get('listingUrl') else {})})
    return result
