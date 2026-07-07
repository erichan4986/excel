import os
import re
import pandas as pd
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from datetime import datetime

from core.database import get_session, FundFairValue


@dataclass
class ParsedValue:
    value: Optional[float] = None
    status: str = 'ok'  # 'ok' | 'missing' | 'invalid'

# Header keyword mappings: canonical_name -> list of detection keywords
_HEADER_KEYWORDS = {
    'project_name': ['公司', '企业', '项目名称', '被投企业'],
    'cost': ['成本'],
    'fair_value': ['公允价值'],
    'total_return': ['退出', '回收', '累计退出', '回收资金'],
    'remark': ['备注'],
    'last_payment_date': ['回款', '日期'],
}


def _detect_period_from_filename(file_path: str) -> Optional[str]:
    """Extract YYYY-MM-DD period from filename like ..._20260331.xlsx."""
    basename = os.path.basename(file_path)
    m = re.search(r'(20\d{2})(\d{2})(\d{2})', basename)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def _identify_headers(row_values: List) -> Dict[str, int]:
    """Map canonical column names to indices by keyword matching."""
    result = {}
    used_indices = set()

    for canonical, keywords in _HEADER_KEYWORDS.items():
        for idx, val in enumerate(row_values):
            if idx in used_indices:
                continue
            if pd.isna(val):
                continue
            text = str(val).strip()
            if any(kw in text for kw in keywords):
                result[canonical] = idx
                used_indices.add(idx)
                break

    # Fallback: if project_name not found, assume first non-empty column
    if 'project_name' not in result:
        for idx, val in enumerate(row_values):
            if not pd.isna(val):
                result['project_name'] = idx
                break

    return result


def _normalize_value(val) -> ParsedValue:
    """Convert various number formats to float, distinguishing missing/invalid."""
    if pd.isna(val):
        return ParsedValue(None, 'missing')
    if isinstance(val, (int, float)):
        return ParsedValue(float(val), 'ok')
    if isinstance(val, str):
        s = val.strip()
        if not s or s.upper() in ('N/A', 'NA', '无', '未提供', '-', '—', 'NONE', '待确认'):
            return ParsedValue(None, 'missing')
        s = s.replace('（', '(').replace('）', ')')
        if s.startswith('(') and s.endswith(')'):
            s = '-' + s[1:-1]
        s = s.replace(',', '')
        try:
            return ParsedValue(float(s), 'ok')
        except ValueError:
            return ParsedValue(None, 'invalid')
    return ParsedValue(None, 'invalid')


def _parse_date(val) -> Optional[str]:
    """Convert Excel date to YYYY-MM-DD string."""
    if pd.isna(val):
        return None
    if isinstance(val, datetime):
        return val.strftime('%Y-%m-%d')
    if isinstance(val, str):
        val = val.strip()
        if val:
            return val
    return None


def parse_fund_fair_value(file_path: str, original_filename: Optional[str] = None, period_override: Optional[str] = None):
    """
    Parse a multi-sheet fund fair value Excel file.

    Returns:
        (period, list of record dicts, warnings)
        Each record: {fund_name, project_name, period, cost, fair_value, total_return, remark, last_payment_date}
    """
    period = period_override
    if not period:
        period = _detect_period_from_filename(original_filename) if original_filename else None
    if not period:
        period = _detect_period_from_filename(file_path)
    if not period:
        return None, [], []

    xl = pd.ExcelFile(file_path)
    all_records = []
    warnings = []

    for sheet_name in xl.sheet_names:
        df = pd.read_excel(file_path, sheet_name=sheet_name, header=None)
        if len(df) < 3:
            continue

        # Header is typically row 1 (0-based index 1), skip empty row 0
        header_row_idx = 1
        for r in range(min(5, len(df))):
            non_empty = [c for c in df.iloc[r] if pd.notna(c)]
            if len(non_empty) >= 4:
                header_row_idx = r
                break

        header_values = [df.iloc[header_row_idx, c] for c in range(len(df.columns))]
        col_map = _identify_headers(header_values)

        # Parse data rows (after header)
        for row_idx in range(header_row_idx + 1, len(df)):
            row = df.iloc[row_idx]

            # Get project name
            pn_col = col_map.get('project_name', 0)
            project_name = str(row.iloc[pn_col]).strip() if pd.notna(row.iloc[pn_col]) else ''

            # Skip total/summary rows and empty rows
            if not project_name or '合计' in project_name or project_name in ('nan', 'None', ''):
                continue

            cost_pv = _normalize_value(row.iloc[col_map.get('cost', 1)]) if 'cost' in col_map else ParsedValue(0.0, 'ok')
            fair_value_pv = _normalize_value(row.iloc[col_map.get('fair_value', 2)]) if 'fair_value' in col_map else ParsedValue(0.0, 'ok')
            total_return_pv = _normalize_value(row.iloc[col_map.get('total_return', 3)]) if 'total_return' in col_map else ParsedValue(0.0, 'ok')

            if cost_pv.status == 'invalid':
                warnings.append(f"{project_name} 成本无法解析: {row.iloc[col_map.get('cost', 1)]}")
            if fair_value_pv.status == 'invalid':
                warnings.append(f"{project_name} 公允价值无法解析: {row.iloc[col_map.get('fair_value', 2)]}")
            if total_return_pv.status == 'invalid':
                warnings.append(f"{project_name} 累计退出无法解析: {row.iloc[col_map.get('total_return', 3)]}")

            record = {
                'fund_name': str(sheet_name).strip(),
                'project_name': project_name,
                'period': period,
                'cost': cost_pv.value,
                'fair_value': fair_value_pv.value,
                'total_return': total_return_pv.value,
                'remark': str(row.iloc[col_map.get('remark', 4)]).strip() if col_map.get('remark') and pd.notna(row.iloc[col_map.get('remark')]) else None,
                'last_payment_date': _parse_date(row.iloc[col_map.get('last_payment_date', 5)]) if col_map.get('last_payment_date') else None,
                'source_file': os.path.basename(file_path),
            }
            all_records.append(record)

    return period, all_records, warnings


def store_fund_records(records: List[Dict], overwrite: bool = False):
    """Store parsed fund records to database."""
    if not records:
        return {'status': 'warning', 'message': '没有解析到数据'}

    session = get_session()
    try:
        # Group by fund_name + period for deduplication
        groups = {}
        for r in records:
            key = (r['fund_name'], r['period'])
            groups.setdefault(key, []).append(r)

        # Check for existing records when overwrite is False
        duplicates = []
        if not overwrite:
            for (fund_name, period) in groups:
                existing = session.query(FundFairValue).filter_by(
                    fund_name=fund_name, period=period
                ).first()
                if existing:
                    duplicates.append({'fund_name': fund_name, 'period': period})

        if duplicates:
            return {
                'status': 'confirm',
                'duplicates': duplicates,
                'message': '以下基金期间已存在，确认覆盖？'
            }

        stored = 0
        for (fund_name, period), group_records in groups.items():
            if overwrite:
                session.query(FundFairValue).filter_by(
                    fund_name=fund_name, period=period
                ).delete()

            for rec in group_records:
                session.add(FundFairValue(**rec))
                stored += 1

        session.commit()
        return {
            'status': 'success',
            'records_stored': stored,
            'funds': len(groups),
            'period': period
        }
    except Exception as e:
        session.rollback()
        return {'status': 'error', 'message': str(e)}
    finally:
        session.close()


def preview_fund_fair_value(file_path: str, original_filename: Optional[str] = None, period_override: Optional[str] = None) -> Dict:
    """Preview parsed fund data without storing."""
    period, records, warnings = parse_fund_fair_value(
        file_path, original_filename=original_filename, period_override=period_override
    )
    if period is None:
        return {'status': 'confirm', 'error': '无法从文件名识别基金期间，请确认或手动指定'}
    if not records:
        return {'status': 'error', 'error': '未能解析到任何基金数据'}

    # Group by fund for preview display
    fund_stats = {}
    for r in records:
        fn = r['fund_name']
        if fn not in fund_stats:
            fund_stats[fn] = {'count': 0, 'projects': []}
        fund_stats[fn]['count'] += 1
        fund_stats[fn]['projects'].append(r['project_name'])

    return {
        'status': 'success',
        'period': period,
        'total_records': len(records),
        'funds': list(fund_stats.keys()),
        'fund_stats': fund_stats,
        'records': records
    }
