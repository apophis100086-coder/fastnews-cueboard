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
DRAFT_V64=deepcopy(DRAFT)
DRAFT_V64['properties']['editorial_review']=obj(
    main_news_first=S,independent_information=S,product_link=S,
    repeated_mechanisms=S,generic_endings=S,product_manual_style=S,
    material_sufficient=B,issues=arr(S))
DRAFT_V64['required']=list(DRAFT_V64['properties'])
