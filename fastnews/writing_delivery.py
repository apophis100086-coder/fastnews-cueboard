"""One clean publication view for preview, clipboard, Markdown and Word.

Evidence URLs and raw text belong to the separate trace, never this view.
"""
from io import BytesIO
import re
from xml.sax.saxutils import escape
from zipfile import ZipFile,ZIP_DEFLATED


def clean(text):
    text=re.sub(r'\[([^\]]+)\]\(https?://[^)]+\)',r'\1',str(text or ''))
    text=re.sub(r'(?i)(?:https?://|www\.)[^\s<>\u3000\u3001\u3002\uff1b\uff09)]+','',text)
    return text.strip()


def source_line(refs):
    parts=[]
    for source in refs:
        title=' '.join(clean(source.get('title') or source.get('name')).split())
        name=' '.join(clean(source.get('name')).split())
        day=clean(source.get('published_at') or '日期待核')
        day=day[:10] if re.match(r'^\d{4}-\d{2}-\d{2}',day) else day
        label=(name+'《'+title+'》' if name and name!=title else title)+'，'+day
        period=clean(source.get('data_period'))
        if period:label+='；数据统计期：'+period
        if label not in parts:parts.append(label)
    return '（信息／数据来源：'+'；'.join(parts)+'）' if parts else '（信息来源待补齐）'


def attach_appendix(content,items,snapshot,checks):
    from .writing_checks import normalized,numeric_tokens,number_units,changed_quantity_scopes,within_cutoff,issue
    sources={s['id']:s for s in snapshot['sources']};accepted=[];missing=[]
    members=snapshot.get('product_definition',{}).get('member_codes',[snapshot['product_code']])
    for item in items:
        source=sources.get(item['source_id']);problem=None
        if not source or not source.get('text_observed') or not source.get('date_observed') or not within_cutoff(source.get('published_at'),snapshot['news_cutoff']):problem='备注来源尚未核实'
        elif item['subject_code'] not in members or (source.get('subject_code') and item['subject_code']!=source['subject_code']):problem='备注与成员产品不对应'
        elif not item['quote'].strip() or not any(normalized(item['quote']) in normalized(p) for p in source.get('text_segments',[source.get('text','')])):problem='备注引文没有对应已读原文'
        elif numeric_tokens(item['text'])-numeric_tokens(item['quote']+' '+item['subject_code']) or number_units(item['text'])-number_units(item['quote']) or changed_quantity_scopes(item['text'],item['quote']):problem='备注数字与原文不一致'
        if problem:
            missing.append(problem+'：'+item['subject_code']);continue
        accepted.append(dict(item,sources=[dict(source_id=source['id'],name=source['name'],title=source['title'],url=source.get('url'),published_at=source['published_at'],data_period='')]))
    for code in members:
        if not any(p['subject_code']==code and p['kind']=='risk' for p in accepted):missing.append(code+' 特殊风险备注待补齐')
    content['appendix']=accepted
    content['appendix_missing']=list(dict.fromkeys(missing))
    checks['appendix']={'status':'pending' if missing else 'checked','missing':content['appendix_missing']}
    checks['editorial']['notes'].extend(content['appendix_missing'])


def blocks(content):
    yield 'title',clean(content['title'])
    for p in content.get('paragraphs',[]):
        yield 'body',clean(p['text'])
        yield 'source',source_line(p.get('sources',[]))
    notes=content.get('appendix',[])
    for kind,label in [('product_note','产品备注'),('risk','风险提示')]:
        entries=[p for p in notes if p['kind']==kind]
        if entries:
            yield 'heading',label
            for p in entries:
                yield 'note',clean(p['text'])
                yield 'source',source_line(p['sources'])
    general=content.get('risk_notice',{}).get('text','')
    if general:
        if not any(p['kind']=='risk' for p in notes):yield 'heading','风险提示'
        yield 'note',clean(general)


def render(content):
    return '\n\n'.join(text for _,text in blocks(content))


def word(content):
    """Minimal standards-based OOXML, A4 portrait and restrained Chinese styles."""
    parts=[]
    for role,text in blocks(content):
        style={'title':'Title','body':'Normal','source':'Source','heading':'Heading1','note':'Note'}[role]
        parts.append('<w:p><w:pPr><w:pStyle w:val="'+style+'"/></w:pPr><w:r><w:t xml:space="preserve">'+escape(text)+'</w:t></w:r></w:p>')
    ns='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    document='<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="'+ns+'"><w:body>'+''.join(parts)+'<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="567" w:footer="567"/></w:sectPr></w:body></w:document>'
    styles='<w:styles xmlns:w="'+ns+'"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="宋体"/><w:sz w:val="24"/><w:color w:val="000000"/></w:rPr></w:rPrDefault></w:docDefaults>'
    for sid,size,bold,spacing in [('Normal',24,False,150),('Title',32,True,250),('Source',20,False,150),('Heading1',24,True,160),('Note',21,False,120)]:
        styles+='<w:style w:type="paragraph" w:styleId="'+sid+'"><w:name w:val="'+sid+'"/><w:pPr><w:spacing w:after="'+str(spacing)+'" w:line="330" w:lineRule="auto"/><w:widowControl/>'+('<w:keepNext/>' if bold else '')+'</w:pPr><w:rPr><w:sz w:val="'+str(size)+'"/>'+('<w:b/>' if bold else '')+'</w:rPr></w:style>'
    styles+='</w:styles>'
    content_types='<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>'
    rels='<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    output=BytesIO()
    with ZipFile(output,'w',ZIP_DEFLATED) as z:
        for path,data in [('[Content_Types].xml',content_types),('_rels/.rels',rels),('word/document.xml',document),('word/styles.xml',styles),('word/_rels/document.xml.rels',rels.replace('officeDocument"','styles"').replace('word/document.xml','styles.xml'))]:z.writestr(path,data.encode('utf-8'))
    return output.getvalue()
