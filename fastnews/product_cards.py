"""Public legal product identities; never evidence for current holdings."""
from pathlib import Path
import yaml


def registry():
    path=Path(__file__).resolve().parents[1]/'config/product_cards.yaml'
    return yaml.safe_load(path.read_text(encoding='utf-8'))['products']


def product_aliases():
    return [name for card in registry().values() for name in [card['name'],*card['aliases']]]


def index_names():
    return [name for card in registry().values() for name in card['internal_index_names']]
