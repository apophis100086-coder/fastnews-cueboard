"""Editorial review is separate from fact/policy gates and human acceptance."""
from difflib import SequenceMatcher
import re
from .writing_checks import issue


def quality_checks(raw, characters, v64):
    review=raw.get('editorial_review')
    result=dict(version='v6.4' if v64 else 'legacy',model_review=review,
        human_status='pending',target_edit_minutes=10,actual_edit_minutes=None,errors=[],notes=[])
    if not v64:
        result['notes'].append('旧版稿件未按V6.4完成编辑质量检查')
        return result
    if not review['material_sufficient']:
        result['errors'].append(issue('editorial_material','模型认为独立材料不足以形成完整稿件'))
    result['errors'] += [issue('editorial_review',message) for message in review['issues']]
    if raw['status']=='ready' and not 950<=characters<=1250:
        result['errors'].append(issue('editorial_length',f'正文{characters}字，与约1100字目标差距较大；先检查材料，不能用分析填充'))
    paragraphs=[p['text'] for p in raw['paragraphs']]
    for i,text in enumerate(paragraphs):
        for prior in paragraphs[:i]:
            if len(text)>50 and SequenceMatcher(None,text,prior).ratio()>.72:
                result['errors'].append(issue('editorial_repetition','段落存在大幅重复，需检查信息增量',str(i+1)))
                break
        # This is a narrow known regression, not a substitute for semantic review.
        if re.search(r'跨市场推广的空间能否打开|仍需观察[。！]?\s*$|有待验证[。！]?\s*$',text):
            result['errors'].append(issue('editorial_ending','出现无新增信息的对冲收尾，需删除或用具体事实收束',str(i+1)))
        if re.search(r'(?:具体实施情况|相关措施.{0,8}实施).{0,12}以正式发布为准|不能据此推断.{0,20}(?:基金持仓|当前持仓)',text):
            result['errors'].append(issue('editorial_internal_caveat','内部核验边界进入新闻正文；保留计划或适用范围，删除无信息增量的提示',str(i+1)))
        if re.search(r'(?:\d{4}年.{0,14}(?:资料概要|招募说明书)|(?:资料概要|招募说明书).{0,14}\d{4}年)',text):
            result['errors'].append(issue('editorial_product_provenance','产品资料日期与核验叙述应放入逐段来源区，正文直接说明有依据的相关特点',str(i+1)))
    return result
