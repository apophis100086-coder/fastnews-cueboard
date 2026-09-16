"""Reviewed extra channels of existing publishers, separate from the 26 originals."""
from copy import deepcopy
from pathlib import Path
import re
from urllib.parse import urlsplit

from .config import ConfigError, SOURCE_IDS, _read
from .metadata import normalize_url

ROOT = Path(__file__).resolve().parents[1] / 'config'


def with_channels(config, path=None):
    result = deepcopy(config)
    doc = _read(Path(path).parent if path else ROOT, Path(path).name if path else 'source_channels.yaml')
    if not isinstance(doc, dict) or set(doc) != {'version', 'note', 'channels'} or doc['version'] != 1:
        raise ConfigError('invalid source channel overlay')
    if not isinstance(doc['channels'], dict) or len(doc['channels']) > 30:
        raise ConfigError('invalid source channels')
    groups = {}
    for sid, row in doc['channels'].items():
        if (not re.fullmatch(r'[a-z][a-z0-9_]{2,60}', sid) or sid in SOURCE_IDS
                or not isinstance(row, dict) or set(row) != {'parent', 'name', 'url', 'categories', 'note'}
                or row['parent'] not in SOURCE_IDS):
            raise ConfigError('invalid source channel identity')
        parent = config['sources'][row['parent']]
        url = normalize_url(row['url'])
        if urlsplit(url).hostname != urlsplit(parent['home_url']).hostname:
            raise ConfigError('extra channels must stay at an existing publisher hostname')
        if (not isinstance(row['name'], str) or not 1 <= len(row['name']) <= 80
                or not isinstance(row['note'], str) or not row['note']
                or not isinstance(row['categories'], list) or not row['categories']
                or set(row['categories']) - {'policy', 'hk', 'medicine', 'technology', 'agriculture', 'metals'}):
            raise ConfigError('invalid source channel description')
        # Idempotent: retain configured user preferences when already expanded.
        if sid not in result['sources']:
            result['sources'][sid] = dict(parent, id=sid, name=row['name'], home_url=url,
                discovery_url=url, discovery_mode='reviewed_channel', morning_scan=False,
                notes=row['note'], tags=[], enabled=True)
            result['source_runtime'][sid] = {'capability_status':'needs_probe', 'access_review':'not_checked'}
        groups[sid] = row['categories']
    result['source_channel_categories'] = groups
    return result
