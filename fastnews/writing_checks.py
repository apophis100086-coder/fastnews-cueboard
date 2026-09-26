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
    return {str(Decimal(n).normalize()) for n in re.findall(r'(?<![A-Za-z])\d+(?:\.\d+)?', text)}


def number_units(text):
    text=unicodedata.normalize('NFKC',text).replace(',','')
    return {(str(Decimal(number).normalize()),unit.replace('人民币','')) for number,unit in QUANTITY.findall(text)}


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
    text=re.sub(r'\s+','',text)
    try:
        value=date.fromisoformat(day[:10])
    except (ValueError,TypeError):
        return False
    y,m,d=value.year,value.month,value.day
    return bool(re.search(fr'{y}(?:年|[-/.])0?{m}(?:月|[-/.])0?{d}(?:日|\b)',text))


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


def check_text(text, *, location='', entities=(), product_codes=(), cutoff=None, editorial=False):
    result=[]
    body=unicodedata.normalize('NFKC',text or '')
    if editorial:
        # Only editing instructions are excluded; the finished draft never uses
        # this mode. A forbidden fact elsewhere in the same line is still checked.
        body='。'.join(clause for clause in re.split(r'[。；;，,\n]',body)
            if not re.search(r'不使用|不写|不得|禁止|避免|不涉及|删除|剔除|不采用|不沿用|不能(?:写|推出|推断|断言)|无需',clause))
    matched=set()
    for e in [*known_entities(),*entities]:
        if e.get('name') and e['name'] not in FUND_MANAGER_NAMES and e['name'] in body and e.get('market') in {'CN','UNKNOWN'}:
            if e['name'] in matched: continue
            matched.add(e['name'])
            result.append(issue('a_share_company' if e['market']=='CN' else 'unknown_company',
                '不能出现A股具体上市公司' if e['market']=='CN' else '公司上市身份待核实',location,e['name']))
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
                result.append(issue('product_flow','产品规模、总份额或净资金数据不得进入公开稿',location,sentence));break
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
    if number_units(claim['statement'])-number_units(quote):
        errors.append(issue('unsupported_unit','陈述的数字、单位或币种组合与原文不同',claim['id']))
    if changed_quantity_scopes(claim['statement'],quote):
        errors.append(issue('unsupported_quantity_scope','陈述改变了原文数值的约数或上下限口径',claim['id']))
    return errors


def reference_date(source):
    """The cutoff-frozen date of an imported, traceable product reference."""
    return (source.get('data_as_of') or '') if source.get('evidence_id') and source.get('date_observed') else ''
