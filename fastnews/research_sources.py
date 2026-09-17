"""Public research-only registry overlay; no implied collector capability."""
from pathlib import Path
import re

from .config import ConfigError, _read
from .metadata import normalize_url

ROOT = Path(__file__).resolve().parents[1] / 'config'


def add_research_sources(config):
    doc = _read(ROOT, 'research_sources.yaml')
    if (not isinstance(doc, dict) or set(doc) != {'version', 'note', 'sources'} or
            doc['version'] != 1 or not isinstance(doc['sources'], dict) or len(doc['sources']) > 20):
        raise ConfigError('invalid research source overlay')
    for sid, row in doc['sources'].items():
        if (not re.fullmatch(r'[a-z][a-z0-9_]{2,60}', sid) or not isinstance(row, dict) or
                set(row) != {'name', 'tier', 'url', 'categories', 'note'} or
                row['tier'] not in {'T1_PRIMARY', 'T2_PROFESSIONAL', 'T3_FINANCIAL_MEDIA', 'T4_OTHER'} or
                any(not isinstance(row[k], str) or not row[k].strip() for k in ('name', 'note')) or
                not isinstance(row['categories'], list) or not row['categories'] or
                set(row['categories']) - {'policy', 'hk', 'medicine', 'technology', 'agriculture', 'metals'}):
            raise ConfigError('invalid research source identity')
        url = normalize_url(row['url'])
        if sid in config['sources'] and config['sources'][sid].get('discovery_mode') != 'research_only':
            raise ConfigError('research source cannot replace an existing publisher')
        if sid not in config['sources']:
            config['sources'][sid] = dict(id=sid, name=row['name'], home_url=url,
                discovery_mode='research_only', enabled=True, morning_scan=False,
                fetch_full_text_in_morning=False, obey_robots_txt=True, rate_limit_per_minute=10,
                tier=row['tier'], tags=[], notes=row['note'])
            config['source_runtime'][sid] = {'capability_status': 'needs_probe', 'access_review': 'not_checked'}
        config['source_channel_categories'][sid] = row['categories']
    return config
