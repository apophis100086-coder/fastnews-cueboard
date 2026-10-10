"""Local task caps; not a claim of control over a host's internal API calls."""
from pathlib import Path
import yaml

PATH=Path(__file__).resolve().parents[1]/'config'/'reader.defaults.yaml'
BOUNDS={'timeout_seconds':(10,600),'max_input_characters':(1000,24000),
        'max_tasks_per_day':(1,50),'max_product_items':(1,60),
        'max_morning_items':(1,60),'max_web_sources':(1,60),'max_refs_per_item':(1,4)}


def reader_limits():
    config=yaml.safe_load(PATH.read_text(encoding='utf-8'))
    if not isinstance(config,dict) or set(config)!=set(BOUNDS):
        raise ValueError('reader.defaults.yaml 的预算字段不完整')
    for key,(lower,upper) in BOUNDS.items():
        if key=='max_tasks_per_day' and config[key] is None:
            continue
        if type(config[key]) is not int or not lower<=config[key]<=upper:
            raise ValueError('无效的宿主任务预算：'+key)
    return config
