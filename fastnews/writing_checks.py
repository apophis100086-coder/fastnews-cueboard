"""Deterministic writing gates. Passing these gates is not regulatory approval."""
import calendar
from datetime import date
import re
import unicodedata
from decimal import Decimal
from pathlib import Path
import yaml

from .metadata import time_bounds, parse_instant

FUND_MANAGER_NAMES={'华泰柏瑞基金管理有限公司','华泰柏瑞基金'}
QUANTITY = re.compile(r'(\d+(?:\.\d+)?)\s*((?:万亿|亿|万|千|百)?(?:人民币元|亿美元|亿港元|美元|港元|亿元|万元|元|亿块|万块|块|亿份|万份|份)|%|个百分点)')


def known_entities():
    registry=yaml.safe_load((Path(__file__).resolve().parents[1]/'config'/'writing_entities.yaml').read_text(encoding='utf-8'))
    return [dict(name=name,market=group['market']) for group in registry['groups'] for name in group['names']]


def normalized(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value or '')).casefold()


def issue(code, message, location='', excerpt=''):
    return dict(code=code,message=message,location=location,excerpt=excerpt[:240],severity='error')


def numeric_tokens(text):
    # Compare magnitudes and explicit dates without allowing invented arithmetic.
    text=unicodedata.normalize('NFKC',text).replace(',', '')
    result={str(Decimal(n).normalize()) for n in re.findall(r'(?<![A-Za-z])\d+(?:\.\d+)?', text)}
    # Literal English durations can be written with Arabic digits in Chinese.
    # Do not reinterpret compound, fractional or scaled number phrases.
    words=dict(zip('one two three four five six seven eight nine ten eleven twelve'.split(),range(1,13)))
    for match in re.finditer(r'(?<![\w-])('+'|'.join(words)+r')\s+(?:months?|weeks?|days?|years?)\b',text,re.I):
        prefix=text[:match.start()].rstrip().lower()
        if re.search(r'\b(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion|and|point|minus)\s*$',prefix):continue
        result.add(str(words[match[1].lower()]))
    return result


def table_number_units(text,context):
    """Read a four-column quantity/growth table only with its explicit header."""
    lines=lambda value:[line.strip() for line in unicodedata.normalize('NFKC',value).splitlines() if line.strip()]
    header=lines(context)
    expected=['绝对量','同比增长','(%)','绝对量','同比增长','(%)']
    if header.count('绝对量')!=2 or not any(header[i:i+6]==expected for i in range(len(header))):return set()
    rows=lines(text);result=set()
    for i,label in enumerate(rows):
        match=re.fullmatch(r'.+\((亿块|万块|块|亿元|万元|元|亿份|万份|份)\)',label)
        values=rows[i+1:i+5]
        if not match or len(values)!=4 or not all(re.fullmatch(r'-?\d+(?:\.\d+)?',v) for v in values):continue
        if i+5<len(rows) and re.fullmatch(r'-?\d+(?:\.\d+)?',rows[i+5]):continue
        for value,unit in zip(values,[match[1],'%',match[1],'%']):
            result.add((str(Decimal(value).normalize()),unit))
    return result


def number_units(text,*,table_context=None):
    text=unicodedata.normalize('NFKC',text).replace(',','')
    # PDF layout may split a Chinese unit, but never join adjacent numeric rows.
    for unit in ('人民币元','亿美元','亿港元','万元','亿元','美元','港元','亿块','万块','亿份','万份','个百分点'):
        text=re.sub(r'\s*'.join(re.escape(c) for c in unit),unit,text)
    result={(str(Decimal(number).normalize()),unit.replace('人民币','')) for number,unit in QUANTITY.findall(text)}
    return result|table_number_units(text,table_context if table_context is not None else text)


def quantity_scopes(text):
    """Preserve explicit bounds/approximation; this does not infer statistical subjects."""
    text=re.sub(r'\s+','',unicodedata.normalize('NFKC',text).replace(',',''))
    result={}
    prefixes=[('ge',r'不低于|不少于|至少|大于等于|>=|≥'),
              ('le',r'不高于|不多于|不超过|至多|最多|小于等于|<=|≤'),
              ('gt',r'超过|高于|大于|多于|超|>'),('lt',r'低于|小于|少于|不足|不到|<'),
              ('approx',r'大约|约为|约|接近|近')]
    for match in QUANTITY.finditer(text):
        scope='exact'
        before,after=text[:match.start()],text[match.end():]
        for label,pattern in prefixes:
            if re.search('(?:'+pattern+')$',before):
                scope=label;break
        for label,pattern in [('ge',r'^(?:及)?以上'),('le',r'^(?:及)?以下'),('approx',r'^(?:左右|上下)')]:
            if re.search(pattern,after):
                scope=label;break
        key=(str(Decimal(match[1]).normalize()),match[2].replace('人民币',''))
        result.setdefault(key,set()).add(scope)
    return result


def changed_quantity_scopes(text, evidence):
    expected=quantity_scopes(evidence)
    return any(key in expected and not scopes<=expected[key] for key,scopes in quantity_scopes(text).items())


def date_present(day, text):
    text=re.sub(r'\s+',' ',text)
    try:
        value=date.fromisoformat(day[:10])
    except (ValueError,TypeError):
        return False
    y,m,d=value.year,value.month,value.day
    return bool(re.search(fr'{y}\s*(?:年|[-/.])\s*0?{m}\s*(?:月|[-/.])\s*0?{d}(?:\s*日|\b|(?=T\d{{2}}:))',text))


def within_cutoff(value, cutoff):
    try:
        lo,hi=time_bounds(value)
        return hi is not None and hi<=parse_instant(cutoff)
    except (ValueError,TypeError):
        return False


def _short_period(sentence):
    if re.search(r'今日|当日|昨日|单日|近[一二三四五六七八九十\d]+[个\s]*(交易日|日|天|周)|本周|上周|周内|本月|上月|月内|季度|季内|上半年|下半年|半年|(?<![\d一二三四五六七八九十])[一二三四五六1-6]\s*个?月',sentence):
        return True
    dates=re.findall(r'(20\d\d)[/年.-](\d{1,2})[/月.-](\d{1,2})',sentence)
    if len(dates)>=2:
        try:
            start,end=[date(*map(int,d)) for d in dates[:2]]
            month=start.month+6
            year=start.year+(month-1)//12
            month=(month-1)%12+1
            boundary=date(year,month,min(start.day,calendar.monthrange(year,month)[1]))
            return end<=boundary
        except ValueError:
            return True
    return None


def check_text(text, *, location='', entities=(), product_codes=(), product_names=(), cutoff=None, editorial=False, rules=None):
    result=[]
    body=unicodedata.normalize('NFKC',text or '')
    if editorial:
        # Only editing instructions are excluded; the finished draft never uses
        # this mode. A forbidden fact elsewhere in the same line is still checked.
        body='。'.join(clause for clause in re.split(r'[。；;，,\n]',body)
            if not re.search(r'不使用|不写|不得|禁止|避免|不涉及|删除|剔除|不采用|不沿用|不能(?:写|推出|推断|断言)|无需',clause))
    # Official fund names can contain an index name. Remove only known names.
    from .product_cards import product_aliases, index_names
    for name in sorted(set(product_names)|set(product_aliases()),key=len,reverse=True):
        letters=re.sub(r'\s+','',unicodedata.normalize('NFKC',name))
        body=re.sub(r'\s*'.join(re.escape(c) for c in letters),'本产品',body)
    matched=set()
    for e in [*known_entities(),*entities]:
        if e.get('name') and e['name'] not in FUND_MANAGER_NAMES and e['name'] in body and e.get('market') in {'CN','HK','UNKNOWN'}:
            if e['name'] in matched: continue
            matched.add(e['name'])
            code={'CN':'a_share_company','HK':'hk_company','UNKNOWN':'unknown_company'}[e['market']]
            result.append(issue(code,'标题和正文不能出现A股、港股具体上市公司' if e['market']!='UNKNOWN' else '公司上市身份待核实',location,e['name']))
    compact=normalized(body)
    if not (rules or {}).get('allow_index_identity') and (any(normalized(name) in compact for name in index_names()) or re.search(r'跟踪.{0,35}指数|(?:标的|跟踪)指数|指数(?:名称|代码)',body)):
        result.append(issue('index_identity','跟踪指数仅供内部核对，标题和正文不介绍其名称、代码或机制',location))
    if re.search(r'(?:港股|股票|证券|个股)(?:代码)?\s*[:：]?\s*\d{4,5}\b',body):
        result.append(issue('hk_code','标题和正文不能出现港股股票代码',location))
    # Code requires an equity context: six-digit fund/index codes are legitimate.
    for match in re.finditer(r'(?:股票|个股|证券代码|股票代码)\s*[:：]?\s*((?:60|68|00|30|83|87|92)\d{4})',body):
        if match[1] not in product_codes:
            result.append(issue('a_share_code','正文包含A股股票代码',location,match[0]))
    for sentence in re.split(r'[。！？!?\n]',body):
        if not sentence.strip():
            continue
        product=bool(re.search(r'ETF|基金|产品|本品',sentence,re.I))
        restricted=re.finditer(r'净流入|净流出|净申购|净赎回|净买入|净卖出|资金(?:净增|加仓|增持|涌入|流入|流出|追捧|青睐)|吸金|总份额|总资产净值|份额(?:达|为|增长|减少|增加|下降|突破)|规模(?:达|为|突破|增长|位居|排名|优势)|(?:规模|份额).{0,10}\d+(?:\.\d+)?\s*[万亿]',sentence)
        for finding in restricted:
            before=sentence[:finding.start()]
            scopes=list(re.finditer(r'南向|北向|全市场|市场整体|港股通资金|行业|产业|市场份额|ETF|基金|产品|本品',before,re.I))
            nearest=scopes[-1][0] if scopes else ''
            market_scope=nearest and not re.fullmatch(r'ETF|基金|产品|本品',nearest,re.I)
            if not market_scope:
                left=max(sentence.rfind(mark,0,finding.start()) for mark in '，,；;')+1
                right=min([pos for mark in '，,；;' if (pos:=sentence.find(mark,finding.end()))>=0] or [len(sentence)])
                flow_clause=sentence[left:right]
                precise=bool(re.search(r'(?:规模|份额|净流入|净流出|净申购|净赎回|净买入|净卖出|资金增持|吸金|增持).{0,16}(?:\d+(?:\.\d+)?|[一二三四五六七八九十百千万亿]+)\s*(?:万亿|亿|万|千|百)?(?:元|美元|港元|份)|(?:\d+(?:\.\d+)?|[一二三四五六七八九十百千万亿]+)\s*(?:万亿|亿|万|千|百)?(?:元|美元|港元|份).{0,8}(?:净流入|净申购|增持)',flow_clause))
                if precise and not (rules or {}).get('allow_precise_product_data'):
                    result.append(issue('product_flow','产品规模、份额或净资金精确量值不得进入公开稿',location,sentence));break
                if re.search(r'连续.{0,8}(?:天|日).{0,12}(?:增持|净流入|吸金|净买入)',sentence) and not re.search(r'截至|自.{2,20}至|\d{1,2}月\d{1,2}日',sentence):
                    result.append(issue('flow_period','连续增持趋势需注明统计区间或截止日期',location,sentence));break
        # A benchmark definition names a return series; it reports no return.
        # Only remove the terminal definition token, never a following value.
        performance_text=re.sub(r'((?:业绩比较基准|比较基准)(?:为|是|[:：])\s*[^,，。;；%]{1,60}指数)收益率(?=(?:[(（]使用估值汇率折算[)）]|[,，]\s*使用估值汇率折算)?\s*$)',r'\1',sentence)
        if re.search(r'(?:ETF|基金|指数|产品).{0,45}(?:涨幅|跌幅|涨跌幅|收益率|回报率|上涨|下跌|翻倍|翻番|收涨|收跌)',performance_text,re.I):
            # Dividend yield / operating figures are not price returns.
            if not re.search(r'(?:ETF|基金|指数|产品)[^，,；;]{0,30}(?:营收|收入|利润|产量|销量|设备销售|资本开支).{0,8}(?:同比|环比)',sentence):
                short=_short_period(sentence)
                if '今年以来' in sentence or '年内' in sentence:
                    short=bool(cutoff and parse_instant(cutoff).month<=6)
                if short is not False:
                    # Explicit periods over six months may proceed to semantic checks.
                    if short or not re.search(r'近一年|近[二三四五六七八九十\d]+年|[一二三四五六七八九十\d]+年累计|全年|(?:[7-9]|1[01])个月',sentence):
                        result.append(issue('short_performance','基金/指数涨跌幅期限不明或不超过六个月',location,sentence))
        if re.search(r'稳赚|保本保收益|保证收益|必然受益|必涨|零风险',sentence):
            result.append(issue('certainty','不能承诺收益或确定受益',location,sentence))
    return result


def qualify_claim(claim, sources, cutoff, product_code=None):
    source=sources.get(claim['source_id'])
    errors=[]
    if not source or source.get('material_id')!=claim['material_id']:
        return [issue('unknown_source','陈述未引用本条选材的来源',claim['id'])]
    quote=claim['quote'].strip()
    if not source.get('text_observed') or not quote or not any(quote in part for part in source.get('text_segments',[source.get('text','')])):
        errors.append(issue('unobserved_quote','缺少可独立比对的原文片段',claim['id']))
    if not claim['supported']:
        errors.append(issue('unsupported_claim',claim['reason'] or '陈述与证据关系待核',claim['id']))
    published=claim['published_at'] or source.get('published_at')
    reliable=source.get('date_observed') and published==source.get('published_at')
    publication=claim['publication_quote']
    if not reliable and not (source.get('text_observed') and publication and publication in source.get('text','') and date_present(published,publication)):
        errors.append(issue('publication_unverified','公开日期缺少原文或可靠元数据依据',claim['id']))
    if not within_cutoff(published,cutoff):
        errors.append(issue('cutoff','首次公开时间未知或超过本期截点',claim['id']))
    identity=product_code if product_code and product_code in source.get('text','') else ''
    if numeric_tokens(claim['statement'])-numeric_tokens(quote+' '+(published or '')+' '+claim['data_period']+' '+identity):
        errors.append(issue('unsupported_number','陈述中的数字未出现在对应证据内',claim['id']))
    if numeric_tokens(claim['data_period'])-numeric_tokens(source.get('text','')+' '+(published or '')+' '+reference_date(source)):
        errors.append(issue('unsupported_period','统计期未能对应原文日期',claim['id']))
    if number_units(claim['statement'])-number_units(quote,table_context=source.get('text','')):
        errors.append(issue('unsupported_unit','陈述的数字、单位或币种组合与原文不同',claim['id']))
    if changed_quantity_scopes(claim['statement'],quote):
        errors.append(issue('unsupported_quantity_scope','陈述改变了原文数值的约数或上下限口径',claim['id']))
    return errors


def reference_date(source):
    """The cutoff-frozen date of an imported, traceable product reference."""
    return (source.get('data_as_of') or '') if source.get('evidence_id') and source.get('date_observed') else ''
