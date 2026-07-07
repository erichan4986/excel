import os
import difflib
from core import llm_helper
from core.config import load_config


def get_mappings():
    config = load_config()
    return config.get('mappings', [])


def get_standard_names():
    return [m['standard'] for m in get_mappings()]


_llm_match_log = []


def clear_llm_match_log():
    _llm_match_log.clear()


def get_llm_match_log():
    return list(_llm_match_log)


def match_header(cell_text, use_llm=True, mapping_overrides=None):
    if not cell_text or not isinstance(cell_text, str):
        return None

    cell_text = cell_text.strip()

    # Check user-provided overrides first (including None = skip)
    if mapping_overrides and cell_text in mapping_overrides:
        val = mapping_overrides[cell_text]
        if val is None:
            return None
        return val

    mappings = get_mappings()

    for m in mappings:
        standard = m['standard']
        # Check exact standard name match
        if cell_text == standard:
            return standard
        # Check aliases with difflib
        for alias in m.get('aliases', []):
            score = difflib.SequenceMatcher(None, cell_text, alias).ratio()
            if score >= 0.75:
                return standard
        # Check standard name with difflib
        score = difflib.SequenceMatcher(None, cell_text, standard).ratio()
        if score >= 0.75:
            return standard

    if use_llm:
        candidates = get_standard_names()
        result = llm_helper.smart_match_single(cell_text, candidates)
        if result:
            _llm_match_log.append((cell_text, result))
            return result

    return None


def batch_match(headers):
    candidates = get_standard_names()
    return llm_helper.smart_match_batch(headers, candidates)
