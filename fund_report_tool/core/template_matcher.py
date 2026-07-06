import os
import re
import difflib
from pathlib import Path
from typing import List, Tuple, Optional
import pandas as pd
from core import llm_helper
from core.matcher import load_config
from core.paths import PROJECT_ROOT, TEMPLATES_DIR

# Section boundaries (0-based DataFrame index)
# Template structure:
#   row 0: 项目名称
#   row 1: 所属时间
#   row 2-63: 资产负债表 (62 items)
#   row 64-88: 利润表 (25 items)
#   row 89-123: 现金流量表 (35 items)
_SECTIONS = [
    ("资产负债表", 2, 63),
    ("利润表", 64, 88),
    ("现金流量表", 89, 123),
]

# Common financial prefixes to strip
_PREFIX_PATTERNS = [
    r'^一、', r'^二、', r'^三、', r'^四、', r'^五、', r'^六、',
    r'^减：', r'^加：',
    r'^其中：', r'^其中:', r'^其中',
]

# Parenthetical annotations to strip
_SUFFIX_PATTERN = r'（[^）]*）|\([^)]*\)'


def _get_template_path() -> Optional[str]:
    """Resolve template file path.

    Resolution order:
    1. FUND_REPORT_TEMPLATE environment variable (if set)
    2. PROJECT_ROOT / templates / 财报模版.xlsx
    3. PROJECT_ROOT / 财报模版.xlsx (for backward compatibility)
    4. cwd / templates / 财报模版.xlsx
    5. cwd / 财报模版.xlsx
    """
    candidates = []
    env_template = os.environ.get("FUND_REPORT_TEMPLATE")
    if env_template:
        candidates.append(Path(env_template))
    candidates.extend([
        TEMPLATES_DIR / "财报模版.xlsx",
        PROJECT_ROOT / "财报模版.xlsx",
        Path.cwd() / "templates" / "财报模版.xlsx",
        Path.cwd() / "财报模版.xlsx",
    ])
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def _load_template_items() -> List[dict]:
    """Load standard items from the financial report template."""
    path = _get_template_path()
    if not path:
        return []

    df = pd.read_excel(path, sheet_name=0, header=None)
    items = []
    for section_name, start, end in _SECTIONS:
        for idx in range(start, end + 1):
            if idx < len(df):
                name = str(df.iloc[idx, 0]).strip()
                if name and name not in ('项目名称', '所属时间', 'nan', 'None', ''):
                    items.append({
                        'name': name,
                        'report_type': section_name,
                        'index': idx,
                    })
    return items


# Module-level caches
_template_items = None
_standard_names = None
_name_to_report_type = None


def get_template_items() -> List[dict]:
    global _template_items
    if _template_items is None:
        _template_items = _load_template_items()
    return _template_items


def get_standard_names() -> List[str]:
    global _standard_names
    if _standard_names is None:
        _standard_names = [item['name'] for item in get_template_items()]
    return _standard_names


def get_name_to_report_type() -> dict:
    global _name_to_report_type
    if _name_to_report_type is None:
        _name_to_report_type = {
            item['name']: item['report_type']
            for item in get_template_items()
        }
    return _name_to_report_type


def strip_prefixes(text: str) -> str:
    """Remove common financial prefixes and parenthetical annotations."""
    if not text:
        return text

    # Remove prefixes like 一、, 减：, 其中：, etc.
    for pattern in _PREFIX_PATTERNS:
        text = re.sub(pattern, '', text)

    # Remove parenthetical annotations like （损失以"-"号填列）
    text = re.sub(_SUFFIX_PATTERN, '', text)

    return text.strip()


def get_skip_list() -> List[str]:
    """Read skip_list from config.yaml."""
    config = load_config()
    return config.get('skip_list', [])


def is_skipped(raw_item: str) -> bool:
    """Check if a raw item is in the skip list."""
    skip_list = get_skip_list()
    return raw_item in skip_list


def match_to_template(raw_item: str, use_llm: bool = True) -> Tuple[Optional[str], float, Optional[str]]:
    """
    Match a raw financial item name against the template standard items.

    Matching cascade:
    1. Skip list check
    2. Existing config.yaml mappings (exact match with standard or aliases)
    3. Exact match with template standard name (original or prefix-stripped)
    4. Fuzzy match with difflib (threshold 0.75)
    5. LLM fallback (only if confidence < 0.95)

    Returns:
        (standard_name, confidence, report_type)
        - standard_name: matched template name or None
        - confidence: float 0.0-1.0
        - report_type: 资产负债表/利润表/现金流量表 or None
    """
    if not raw_item or not isinstance(raw_item, str):
        return None, 0.0, None

    raw_item = raw_item.strip()
    if not raw_item or raw_item.lower() in ('项目', '科目', 'nan', 'none', ''):
        return None, 0.0, None

    # Level 0: Skip list
    if is_skipped(raw_item):
        return None, 0.0, None

    # Level 1: Existing config.yaml mappings (only if standard exists in template)
    template_names = set(get_standard_names())
    config = load_config()
    mappings = config.get('mappings', [])
    for m in mappings:
        standard = m['standard']
        if standard not in template_names:
            continue
        aliases = m.get('aliases', [])
        if raw_item == standard or raw_item in aliases:
            report_type = get_name_to_report_type().get(standard)
            return standard, 1.0, report_type

    # Level 1.5: Hardcoded aliases for common split/merge cases
    # These map raw items that don't exist as standalone template items
    # to the combined template items they belong to.
    _HARDCODED_ALIASES = {
        '预付账款': '预付款项',
        '应收账款': '应收票据及应收账款',
        '应收票据': '应收票据及应收账款',
        '应付票据': '应付账款',
    }
    if raw_item in _HARDCODED_ALIASES:
        standard = _HARDCODED_ALIASES[raw_item]
        if standard in template_names:
            report_type = get_name_to_report_type().get(standard)
            return standard, 1.0, report_type

    cleaned_raw = strip_prefixes(raw_item)
    standard_names = get_standard_names()
    name_to_report_type = get_name_to_report_type()

    # Level 2: Exact match with template standard names
    for std in standard_names:
        if raw_item == std or cleaned_raw == std or cleaned_raw == strip_prefixes(std):
            report_type = name_to_report_type.get(std)
            return std, 1.0, report_type

    # Level 3: Fuzzy match with difflib
    best_match = None
    best_score = 0.0

    for std in standard_names:
        score_orig = difflib.SequenceMatcher(None, raw_item, std).ratio()
        score_clean = difflib.SequenceMatcher(None, cleaned_raw, strip_prefixes(std)).ratio()
        score = max(score_orig, score_clean)

        if score > best_score:
            best_score = score
            best_match = std

    if best_match and best_score >= 0.75:
        report_type = name_to_report_type.get(best_match)
        return best_match, round(best_score, 4), report_type

    # Level 4: LLM fallback (only if best_score < 0.95)
    if use_llm and best_score < 0.95:
        result = llm_helper.smart_match_single(raw_item, standard_names)
        if result:
            report_type = name_to_report_type.get(result)
            return result, 0.95, report_type

    return None, round(best_score, 4), None


def classify_match(confidence: float) -> str:
    """Classify match result by confidence threshold."""
    if confidence >= 0.95:
        return 'auto_confirmed'
    elif confidence >= 0.75:
        return 'needs_review'
    else:
        return 'unmatched'
