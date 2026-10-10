"""Public legal product identities; never evidence for current holdings."""
from pathlib import Path
import yaml


def registry(cutoff=None):
    path=Path(__file__).resolve().parents[1]/'config/product_cards.yaml'
    cards=yaml.safe_load(path.read_text(encoding='utf-8'))['products']
    if cutoff:
        from .metadata import parse_instant
        from zoneinfo import ZoneInfo
        day=parse_instant(cutoff).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
        for card in cards.values():
            if card.get('name_effective_at') and day<card['name_effective_at']:card['name']=card['previous_name']
    return cards


def product_aliases():
    return [name for card in registry().values() for name in [card['name'],*card['aliases']]]


def index_names():
    return [name for card in registry().values() for name in card['internal_index_names']]
