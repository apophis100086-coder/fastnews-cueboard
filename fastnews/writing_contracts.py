"""Structured model output contracts; server performs evidence checks separately."""
def obj(**properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}

S={'type':'string'}
B={'type':'boolean'}
def arr(item):
    return {'type':'array','items':item}
def enum(*values):
    return {'type':'string','enum':list(values)}

ENTITY=obj(name=S,market=enum('CN','HK','OTHER','UNKNOWN'),source_id=S,quote=S)
CLAIM=obj(id=S,material_id=S,statement=S,kind=enum('fact','reported','plan','forecast','analysis'),
    source_id=S,quote=S,published_at=S,publication_quote=S,data_period=S,supported=B,reason=S)
PARAGRAPH=obj(id=S,purpose=S,points=arr(S),analysis=S,product_connection=S,
    material_ids=arr(S),claim_ids=arr(S))
OUTLINE=obj(mainline=S,title_directions=arr(S),paragraphs=arr(PARAGRAPH),claims=arr(CLAIM),
    dispositions=arr(obj(material_id=S,role=S,reason=S)),suggestions=arr(S),changes=arr(S),
    gaps=arr(S),blocking_gaps=arr(S),supplement_queries=arr(S),entities=arr(ENTITY))
DRAFT=obj(status=enum('ready','needs_materials'),title=S,
    paragraphs=arr(obj(text=S,claim_ids=arr(S),material_ids=arr(S),outline_paragraph_id=S,outline_anchor=S)),
    issues=arr(S),entities=arr(ENTITY),review=obj(faithful_to_outline=B,claims_supported=B,
        must_include_covered=B,entities_complete=B,no_new_facts=B))
from copy import deepcopy
OUTLINE_RESEARCH=deepcopy(OUTLINE)
OUTLINE_RESEARCH['properties']['supplemental_sources']=arr(obj(
    material_id=S,source_id=S,title=S,publisher=S,url=S,published_at=S,
    excerpt=S,relation=S))
OUTLINE_RESEARCH['properties']['research_summary']=S
OUTLINE_RESEARCH['required']=list(OUTLINE_RESEARCH['properties'])
DRAFT_V64=deepcopy(DRAFT)
DRAFT_V64['properties']['editorial_review']=obj(
    main_news_first=S,independent_information=S,product_link=S,
    repeated_mechanisms=S,generic_endings=S,product_manual_style=S,
    material_sufficient=B,issues=arr(S))
DRAFT_V64['required']=list(DRAFT_V64['properties'])

# The composing call writes prose. Evidence bookkeeping belongs to editing.
NEWS_ARTICLE=obj(status=enum('ready','needs_materials'),title=S,
    paragraphs=arr(obj(text=S,source_ids=arr(S))),issues=arr(S))
NEWS_EDIT=deepcopy(NEWS_ARTICLE)
NEWS_EDIT['properties'].update(
    evidence=arr(obj(paragraph_index={'type':'integer'},source_id=S,statement=S,
        quote=S,kind=enum('fact','reported','plan','forecast','analysis'),
        data_period=S,supported=B,reason=S)),
    entities=arr(ENTITY),review=deepcopy(DRAFT['properties']['review']),
    editorial_review=deepcopy(DRAFT_V64['properties']['editorial_review']),changes=arr(S))
NEWS_EDIT['required']=list(NEWS_EDIT['properties'])

# Product remarks are separate from the news body and have their own evidence.
DIRECT_EDIT=deepcopy(NEWS_EDIT)
DIRECT_EDIT['properties']['evidence']['items']['properties']['paragraph_index']={
    'type':'integer','minimum':1,'description':'正文段落从1开始编号，第一段为1；标题和附录不计入。'}
DIRECT_EDIT['properties']['appendix']=arr(obj(kind=enum('product_note','risk'),text=S,
    subject_code=S,source_id=S,quote=S))
DIRECT_EDIT['required']=list(DIRECT_EDIT['properties'])

# v3 editors only edit prose. The independent audit cannot replace that prose.
EDITORIAL_EDIT=deepcopy(NEWS_ARTICLE)
EDITORIAL_EDIT['properties']['changes']=arr(S)
EDITORIAL_EDIT['required']=list(EDITORIAL_EDIT['properties'])
ARTICLE_AUDIT=obj(**{key:deepcopy(NEWS_EDIT['properties'][key])
    for key in ('evidence','entities','review','editorial_review')})
