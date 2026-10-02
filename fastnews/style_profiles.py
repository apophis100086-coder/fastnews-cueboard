"""Versioned editorial rules with local sample provenance, never news evidence."""
import hashlib
import json
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parents[1]
RULES=ROOT/'config/editorial_style.yaml'


def profile(code, *, library=None, cutoff=None):
    rules=yaml.safe_load(RULES.read_text(encoding='utf-8'))
    path=Path(library) if library else ROOT/'data/writing_samples/library.json'
    samples=json.loads(path.read_text(encoding='utf-8')).get('items',[]) if path.exists() else []
    samples=[s for s in samples if s['product_code']==code and (not cutoff or s['date']<=cutoff[:10])]
    product=rules['products'].get(code,{})
    result=dict(version=rules['version'],rules_hash=hashlib.sha256(RULES.read_bytes()).hexdigest(),
        purpose='style_only_not_current_facts',common=rules['common'],product=product,
        samples=[{k:s[k] for k in ('id','date','sha256','complete','warnings')} for s in samples],
        source_sample_count=len(samples),sample_gaps=[])
    if product and len(samples)<3:result['sample_gaps'].append('本期可用认可样例不足3篇；风格质量尚未验收')
    return result
