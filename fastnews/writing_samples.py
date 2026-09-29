"""User-authorized local examples, isolated from the news evidence registry."""
from pathlib import Path
from collections import Counter
import hashlib
import json
import re
import shutil
import zipfile
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
LIBRARY=ROOT/'data/writing_samples/library.json'
GROUPS={'恒生科技':'513130','科创半导体':'588710','红利全家桶':'dividend_family'}


def docx_text(path):
    ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    with zipfile.ZipFile(path) as archive:
        item=archive.getinfo('word/document.xml')
        if item.file_size>10_000_000:raise ValueError('样例正文过大')
        root=ET.fromstring(archive.read(item))
    return '\n\n'.join(''.join(t.text or '' for t in p.findall('.//w:t',ns))
        for p in root.findall('.//w:body//w:p',ns) if p.findall('.//w:t',ns))


def import_samples(base, *, year=2026, library=LIBRARY):
    """Explicit local import; copies immutable originals, never publishes files."""
    base=Path(base);library=Path(library);items=[]
    for folder,code in GROUPS.items():
        for source in sorted((base/folder).glob('*.docx')):
            if source.name.startswith('~$'):continue
            raw=source.read_bytes();digest=hashlib.sha256(raw).hexdigest()
            text=docx_text(source)
            match=re.match(r'(\d{1,2})\.(\d{1,2})',source.name)
            if not match:raise ValueError('样例文件名缺少日期：'+source.name)
            date=f'{year}-{int(match[1]):02d}-{int(match[2]):02d}'
            destination=library.parent/'originals'/code/(digest+'.docx')
            destination.parent.mkdir(parents=True,exist_ok=True)
            if not destination.exists():shutil.copy2(source,destination)
            warnings=[]
            if code=='513130' and '520500' in text:
                warnings.append('范文把恒生科技ETF代码误写为520500；写作须使用本期核对身份，不沿用此错码。')
            items.append(dict(id='sample_'+digest[:20],product_code=code,title=text.split('\n')[0],
                date=date,date_basis='user_sample_filename_2026',original_name=source.name,
                source_path=str(source.resolve()),local_original=str(destination.resolve()),
                sha256=digest,text=text,complete=True,purpose='style_only_not_current_facts',warnings=warnings))
    if not items:raise ValueError('样例目录中没有可读Word文件')
    content=dict(version='local-examples-20260929',items=items)
    library.parent.mkdir(parents=True,exist_ok=True)
    library.write_text(json.dumps(content,ensure_ascii=False,indent=2),encoding='utf-8')
    return dict(count=len(items),products=dict(Counter(i['product_code'] for i in items)),library=str(library))


def select_examples(code,focus='',*,library=LIBRARY,cutoff=None):
    if not Path(library).exists():return []
    data=json.loads(Path(library).read_text(encoding='utf-8'))
    candidates=[s for s in data['items'] if s['product_code']==code and (not cutoff or s['date']<=cutoff[:10])]
    candidates.sort(key=lambda s:(s['date'],s['id']),reverse=True)
    if not candidates:return []
    grams=lambda value:{value[i:i+2] for i in range(len(value)-1) if not value[i:i+2].isspace()}
    target=grams(focus)
    chosen=[candidates[0]]
    if len(candidates)>1:
        chosen.append(max(candidates[1:],key=lambda s:(len(target & grams(s['text'])),s['date'])))
    return [{k:s[k] for k in ('id','product_code','title','date','sha256','text','complete','purpose','warnings')}
        for s in chosen]
