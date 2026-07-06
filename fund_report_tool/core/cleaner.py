import os
import re
import json
import pandas as pd
from datetime import datetime
from core.database import (
    get_session, ProjectFinancial, FundFinancial,
    ProjectMetric, FundMetric, CleanLog
)
from core import matcher, metrics
from core import template_matcher
from core import fund_parser
from core.paths import CLEANED_DIR


def normalize_value(val):
    """Convert various number formats to float."""
    if pd.isna(val):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        val = val.strip()
        val = val.replace('（', '(').replace('）', ')')
        if val.startswith('(') and val.endswith(')'):
            val = '-' + val[1:-1]
        val = val.replace(',', '')
        if val.endswith('%'):
            val = val[:-1]
            try:
                return float(val) / 100
            except ValueError:
                return 0.0
        try:
            return float(val)
        except ValueError:
            return 0.0
    return 0.0


def detect_period(df, file_path):
    """Detect reporting period from file name or sheet content."""
    basename = os.path.basename(file_path)
    patterns = [
        (r'(20\d{2})[年/-](\d{1,2})[月/-](\d{1,2})', lambda m: f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"),
        (r'(20\d{2})[年/-]?[Qq]([1-4])', lambda m: f"{m.group(1)}-{['03-31','06-30','09-30','12-31'][int(m.group(2))-1]}"),
        (r'(20\d{2})[年/-](\d{1,2})', lambda m: f"{m.group(1)}-{m.group(2).zfill(2)}-30"),
        (r'(20\d{2})', lambda m: f"{m.group(1)}-12-31"),
    ]

    for pattern, formatter in patterns:
        match = re.search(pattern, basename)
        if match:
            return formatter(match)

    for col in df.columns:
        for val in df[col].head(5):
            val_str = str(val) if pd.notna(val) else ''
            for pattern, formatter in patterns:
                match = re.search(pattern, val_str)
                if match:
                    return formatter(match)
    return None


def detect_sheet(xl, keywords):
    """Find sheet containing any of the keywords."""
    for sheet_name in xl.sheet_names:
        for kw in keywords:
            if kw in sheet_name:
                return sheet_name
    return xl.sheet_names[0] if xl.sheet_names else None


def get_item_from_db(session, model, project_name, period, item_name):
    # Try primary name first, then template-mode aliases
    names = [item_name]
    aliases = {
        "总资产": ["资产总计"],
    }
    if item_name in aliases:
        names.extend(aliases[item_name])
    results = []
    for name in names:
        query = session.query(model).filter_by(period=period, item_name=name)
        if hasattr(model, 'project_name'):
            query = query.filter_by(project_name=project_name)
        results.extend(query.all())
    if not results:
        return None
    # Prefer non-zero values; if multiple non-zero, pick the largest absolute
    non_zero = [r for r in results if r.value is not None and r.value != 0]
    if non_zero:
        return max(non_zero, key=lambda r: abs(r.value)).value
    return results[0].value


def try_parse_complex_sheet(xl, sheet_name):
    """Try to parse a single-sheet Excel with multiple statements in complex layout.
    Returns list of {'raw_item', 'value', 'report_type'} or None if not complex format."""
    try:
        df = pd.read_excel(xl, sheet_name=sheet_name, header=None)
    except Exception:
        return None

    title_patterns = [
        (r'资产负债表', '资产负债表'),
        (r'利润表', '利润表'),
        (r'损益表', '利润表'),
        (r'现金流量表', '现金流量表'),
    ]

    # Scan all columns for section titles
    sections = []
    for idx, row in df.iterrows():
        for col_idx, val in enumerate(row):
            val_str = str(val) if pd.notna(val) else ''
            for pattern, report_type in title_patterns:
                if pattern in val_str:
                    sections.append((idx, val_str.strip(), report_type))
                    break
            else:
                continue
            break

    if len(sections) < 2:
        return None

    items = []
    for i, (start_idx, title, report_type) in enumerate(sections):
        end_idx = sections[i + 1][0] if i + 1 < len(sections) else len(df)

        # Find header row
        header_idx = None
        for idx in range(start_idx + 1, min(start_idx + 5, end_idx)):
            row_text = ' '.join(str(v) for v in df.iloc[idx] if pd.notna(v))
            if any(kw in row_text for kw in ['期末余额', '年初余额', '本年金额', '本年发生额', '上期金额', '项目', '科目']):
                header_idx = idx
                break

        if header_idx is None:
            continue

        header_row = df.iloc[header_idx]

        # Detect columns from header row
        item_col = None
        value_col = None
        right_item_col = None
        right_value_col = None

        for col_idx, val in enumerate(header_row):
            val_str = str(val) if pd.notna(val) else ''
            if any(kw in val_str for kw in ['项目', '科目', 'item', '名称', '指标']):
                if item_col is None:
                    item_col = col_idx
            elif any(kw in val_str for kw in ['金额', '期末', '本期', '数值', 'value', '余额', '数', '发生额']):
                if value_col is None:
                    value_col = col_idx

        if item_col is None:
            item_col = 0

        # Fallback: detect value column from data
        if value_col is None:
            for col_idx in range(item_col + 1, len(df.columns)):
                for data_idx in range(header_idx + 1, min(header_idx + 5, end_idx)):
                    val = df.iloc[data_idx, col_idx]
                    if pd.notna(val) and normalize_value(val) != 0:
                        value_col = col_idx
                        break
                if value_col is not None:
                    break

        # Balance sheet: detect right-side columns
        if report_type == '资产负债表':
            for col_idx, val in enumerate(header_row):
                val_str = str(val) if pd.notna(val) else ''
                if any(kw in val_str for kw in ['负债', '权益', '股东']):
                    right_item_col = col_idx
                    for v_col in range(col_idx + 1, len(df.columns)):
                        v_val = str(header_row.iloc[v_col]) if pd.notna(header_row.iloc[v_col]) else ''
                        if any(kw in v_val for kw in ['金额', '期末', '本期', '数值', 'value', '余额', '数']):
                            right_value_col = v_col
                            break
                    if right_value_col is None:
                        for v_col in range(col_idx + 1, len(df.columns)):
                            for data_idx in range(header_idx + 1, min(header_idx + 5, end_idx)):
                                val = df.iloc[data_idx, v_col]
                                if pd.notna(val) and normalize_value(val) != 0:
                                    right_value_col = v_col
                                    break
                            if right_value_col is not None:
                                break
                    break

        def read_item(row, icol, vcol):
            if icol is None or icol >= len(row):
                return None, 0
            raw_item = str(row.iloc[icol]).strip() if pd.notna(row.iloc[icol]) else ''
            if not raw_item or raw_item.lower() in ['nan', 'none', '']:
                return None, 0
            if raw_item.startswith('单位名称:') or '会企' in raw_item:
                return None, 0
            if re.match(r'^[\s.]*$', raw_item):
                return None, 0
            raw_value = row.iloc[vcol] if vcol is not None and vcol < len(row) and pd.notna(row.iloc[vcol]) else 0
            value = normalize_value(raw_value)
            return raw_item, value

        for idx in range(header_idx + 1, end_idx):
            row = df.iloc[idx]
            if row.isna().all():
                continue

            raw_item, value = read_item(row, item_col, value_col)
            if raw_item:
                items.append({'raw_item': raw_item, 'value': value, 'report_type': report_type})

            if right_item_col is not None:
                raw_item, value = read_item(row, right_item_col, right_value_col)
                if raw_item:
                    items.append({'raw_item': raw_item, 'value': value, 'report_type': report_type})

    return items if items else None


def preview_clean(file_path, data_type):
    """Preview mode: extract raw items and match against template without storing."""
    if data_type not in ('project', 'fund'):
        return {"status": "error", "error": "Invalid data_type"}

    if data_type == 'fund':
        return fund_parser.preview_fund_fair_value(file_path)

    try:
        xl = pd.ExcelFile(file_path)
    except Exception as e:
        return {"status": "error", "error": f"无法读取Excel: {e}"}

    keywords = ['资产', '负债', '利润', '损益', '现金'] if data_type == 'project' else ['资产', '负债', '现金', '基金']
    matching_sheets = []
    for sheet_name in xl.sheet_names:
        for kw in keywords:
            if kw in sheet_name:
                matching_sheets.append(sheet_name)
                break
    if not matching_sheets:
        matching_sheets = [xl.sheet_names[0]]

    try:
        df_first = pd.read_excel(xl, sheet_name=matching_sheets[0])
    except Exception as e:
        return {"status": "error", "error": f"无法读取sheet: {e}"}

    period = detect_period(df_first, file_path)
    if not period:
        period = "unknown"

    project_name = os.path.splitext(os.path.basename(file_path))[0]
    if '_' in project_name:
        parts = project_name.rsplit('_', 1)
        if re.match(r'20\d{2}', parts[1]):
            project_name = parts[0]
    # Also strip trailing date patterns without underscore (e.g., gm公司20241231)
    project_name = re.sub(r'(20\d{2})(\d{2})?(\d{2})?$', '', project_name).rstrip('._-')

    preview = {}
    stats = {"total": 0, "auto_confirmed": 0, "needs_review": 0, "unmatched": 0, "auto_skip": 0}

    for sheet_name in matching_sheets:
        # Try complex single-sheet format first
        complex_items = try_parse_complex_sheet(xl, sheet_name)
        if complex_items:
            sheet_preview = []
            for item in complex_items:
                standard_name, confidence, _ = template_matcher.match_to_template(item['raw_item'], use_llm=True)
                status = template_matcher.classify_match(confidence)
                # Auto-skip zero-value items that aren't high-confidence matches
                if status != 'auto_confirmed' and item['value'] == 0.0:
                    status = 'auto_skip'
                sheet_preview.append({
                    "raw": item['raw_item'],
                    "standard": standard_name,
                    "confidence": confidence,
                    "report_type": item['report_type'],
                    "status": status,
                    "value": item['value'],
                })
                stats["total"] += 1
                stats[status] += 1
            preview[sheet_name] = sheet_preview
            continue

        try:
            df = pd.read_excel(xl, sheet_name=sheet_name)
        except Exception:
            continue

        df.columns = [str(c).strip() for c in df.columns]

        item_col = None
        value_col = None
        for col in df.columns:
            if any(kw in col for kw in ['项目', '科目', 'item', '名称', '指标']):
                item_col = col
            elif any(kw in col for kw in ['金额', '期末', '本期', '数值', 'value', '余额', '数']):
                value_col = col

        if item_col is None and len(df.columns) > 0:
            item_col = df.columns[0]
        if value_col is None and len(df.columns) > 1:
            value_col = df.columns[1]

        sheet_preview = []
        for _, row in df.iterrows():
            if item_col is None:
                continue
            raw_item = str(row.get(item_col, '')).strip()
            if not raw_item or raw_item.lower() in ['项目', '科目', 'nan', 'none', '']:
                continue

            raw_value = row.get(value_col, 0) if value_col else 0
            value = normalize_value(raw_value)

            standard_name, confidence, report_type = template_matcher.match_to_template(raw_item, use_llm=True)
            status = template_matcher.classify_match(confidence)

            # Auto-skip zero-value items that aren't high-confidence matches
            if status != 'auto_confirmed' and value == 0.0:
                status = 'auto_skip'

            sheet_preview.append({
                "raw": raw_item,
                "standard": standard_name,
                "confidence": confidence,
                "report_type": report_type,
                "status": status,
                "value": value,
            })

            stats["total"] += 1
            stats[status] += 1

        preview[sheet_name] = sheet_preview

    return {
        "status": "success",
        "preview": preview,
        "project_name": project_name,
        "period": period,
        "stats": stats,
        "standard_names": template_matcher.get_standard_names(),
    }


def clean_and_store(file_path, data_type, db_path=None, overwrite=False, confirmed_mappings=None):
    """Main cleaning function. If confirmed_mappings is provided, only keep mapped items."""
    if data_type == 'fund':
        _, records = fund_parser.parse_fund_fair_value(file_path)
        if not records:
            return {"status": "error", "error": "未能解析到基金数据"}
        # Fund data is always overwritten by period (same-period updates replace old data)
        result = fund_parser.store_fund_records(records, overwrite=True)
        result['period'] = records[0]['period'] if records else None
        result['records_processed'] = len(records)
        return result

    warnings_list = []
    errors_list = []
    metrics_snapshot = {}

    try:
        xl = pd.ExcelFile(file_path)
    except Exception as e:
        return {"status": "error", "warnings": [], "errors": [f"无法读取Excel: {e}"], "metrics": {}}

    # Find ALL matching sheets (balance sheet, income statement, cash flow, etc.)
    keywords = ['资产', '负债', '利润', '损益', '现金'] if data_type == 'project' else ['资产', '负债', '现金', '基金']
    matching_sheets = []
    for sheet_name in xl.sheet_names:
        for kw in keywords:
            if kw in sheet_name:
                matching_sheets.append(sheet_name)
                break
    if not matching_sheets:
        matching_sheets = [xl.sheet_names[0]]

    # Determine project_name and period from file path / first sheet
    try:
        df_first = pd.read_excel(xl, sheet_name=matching_sheets[0])
    except Exception as e:
        return {"status": "error", "warnings": [], "errors": [f"无法读取sheet: {e}"], "metrics": {}}

    period = detect_period(df_first, file_path)
    if not period:
        period = "unknown"
        warnings_list.append("未能自动识别报表期间")

    project_name = os.path.splitext(os.path.basename(file_path))[0]
    if '_' in project_name:
        parts = project_name.rsplit('_', 1)
        if re.match(r'20\d{2}', parts[1]):
            project_name = parts[0]
    # Also strip trailing date patterns without underscore (e.g., gm公司20241231)
    project_name = re.sub(r'(20\d{2})(\d{2})?(\d{2})?$', '', project_name).rstrip('._-')

    session = get_session(db_path=db_path)
    try:
        # Check for duplicates once (at file level)
        existing = False
        if data_type == 'project':
            existing = session.query(ProjectFinancial).filter_by(
                project_name=project_name, period=period
            ).first() is not None
        else:
            existing = session.query(FundFinancial).filter_by(period=period).first() is not None

        if existing and not overwrite:
            return {
                "status": "confirm",
                "duplicates": [{"project_name": project_name if data_type == 'project' else None, "period": period}],
                "warnings": [f"{'项目' + project_name if data_type == 'project' else '基金'} 期间 {period} 已存在数据"],
                "errors": [],
                "metrics": {},
                "period": period,
                "records_processed": 0
            }

        if overwrite or not existing:
            if data_type == 'project':
                session.query(ProjectFinancial).filter_by(
                    project_name=project_name, period=period
                ).delete()
            else:
                session.query(FundFinancial).filter_by(period=period).delete()
            session.commit()

        total_records = 0
        for sheet_name in matching_sheets:
            try:
                df = pd.read_excel(xl, sheet_name=sheet_name)
            except Exception as e:
                warnings_list.append(f"Sheet '{sheet_name}' 读取失败: {e}")
                continue

            report_type = "unknown"
            if '资产' in sheet_name or '负债' in sheet_name:
                report_type = "资产负债表"
            elif '利润' in sheet_name or '损益' in sheet_name:
                report_type = "利润表"
            elif '现金' in sheet_name:
                report_type = "现金流量表"

            records_count = 0

            # Try complex single-sheet format first
            complex_items = try_parse_complex_sheet(xl, sheet_name)
            if complex_items:
                for item in complex_items:
                    raw_item = item['raw_item']
                    value = item['value']
                    report_type = item['report_type']

                    if confirmed_mappings is not None:
                        std_from_template, confidence, tmpl_report_type = template_matcher.match_to_template(raw_item, use_llm=True)
                        if raw_item in confirmed_mappings:
                            standard_name = confirmed_mappings[raw_item]
                            if standard_name is None:
                                continue
                        elif std_from_template and confidence >= 0.95:
                            standard_name = std_from_template
                        else:
                            continue
                        if tmpl_report_type:
                            report_type = tmpl_report_type
                    else:
                        standard_name = matcher.match_header(raw_item, use_llm=True)
                        if not standard_name:
                            standard_name = raw_item

                    if data_type == 'project':
                        record = ProjectFinancial(
                            project_name=project_name, period=period,
                            report_type=report_type, item_name=standard_name,
                            value=value, source_file=file_path,
                            uploaded_at=datetime.now()
                        )
                    else:
                        record = FundFinancial(
                            period=period, report_type=report_type,
                            item_name=standard_name, value=value,
                            source_file=file_path, uploaded_at=datetime.now()
                        )
                    session.add(record)
                    records_count += 1
                session.commit()
                total_records += records_count

                # Save CSV snapshot per sheet
                CLEANED_DIR.mkdir(parents=True, exist_ok=True)
                snapshot_name = f"{os.path.basename(file_path)}_{sheet_name}_{period}_cleaned.csv"
                snapshot_path = CLEANED_DIR / snapshot_name
                df.to_csv(str(snapshot_path), index=False, encoding='utf-8-sig')
                continue

            # Standard format parsing
            df.columns = [str(c).strip() for c in df.columns]

            item_col = None
            value_col = None
            for col in df.columns:
                if any(kw in col for kw in ['项目', '科目', 'item', '名称', '指标']):
                    item_col = col
                elif any(kw in col for kw in ['金额', '期末', '本期', '数值', 'value', '余额', '数']):
                    value_col = col

            if item_col is None and len(df.columns) > 0:
                item_col = df.columns[0]
            if value_col is None and len(df.columns) > 1:
                value_col = df.columns[1]

            for _, row in df.iterrows():
                if item_col is None:
                    continue
                raw_item = str(row.get(item_col, '')).strip()
                if not raw_item or raw_item.lower() in ['项目', '科目', 'nan', 'none', '']:
                    continue

                raw_value = row.get(value_col, 0) if value_col else 0
                value = normalize_value(raw_value)

                # Template mode: use confirmed_mappings to filter and map
                if confirmed_mappings is not None:
                    std_from_template, confidence, tmpl_report_type = template_matcher.match_to_template(raw_item, use_llm=True)
                    # User may have overridden the mapping in the preview
                    if raw_item in confirmed_mappings:
                        standard_name = confirmed_mappings[raw_item]
                        # Explicit None means user chose to skip this item
                        if standard_name is None:
                            continue
                    elif std_from_template and confidence >= 0.95:
                        # Auto-confirmed high-confidence match
                        standard_name = std_from_template
                    else:
                        # Not in confirmed mappings and not auto-confirmed: skip
                        continue
                    # Use template-derived report_type when available
                    if tmpl_report_type:
                        report_type = tmpl_report_type
                else:
                    # Legacy mode: existing matcher logic
                    standard_name = matcher.match_header(raw_item, use_llm=True)
                    if not standard_name:
                        standard_name = raw_item

                if data_type == 'project':
                    record = ProjectFinancial(
                        project_name=project_name, period=period,
                        report_type=report_type, item_name=standard_name,
                        value=value, source_file=file_path,
                        uploaded_at=datetime.now()
                    )
                else:
                    record = FundFinancial(
                        period=period, report_type=report_type,
                        item_name=standard_name, value=value,
                        source_file=file_path, uploaded_at=datetime.now()
                    )
                session.add(record)
                records_count += 1

            session.commit()
            total_records += records_count

            # Save CSV snapshot per sheet
            snapshot_dir = "data/cleaned"
            os.makedirs(snapshot_dir, exist_ok=True)
            snapshot_name = f"{os.path.basename(file_path)}_{sheet_name}_{period}_cleaned.csv"
            snapshot_path = os.path.join(snapshot_dir, snapshot_name)
            df.to_csv(snapshot_path, index=False, encoding='utf-8-sig')

        # Aggregate merge: sum values when multiple raw items map to the same standard name
        from sqlalchemy import func
        if data_type == 'project':
            for report_type in ["资产负债表", "利润表", "现金流量表"]:
                aggregated = session.query(
                    ProjectFinancial.item_name,
                    func.sum(ProjectFinancial.value).label('total_value')
                ).filter_by(
                    project_name=project_name, period=period, report_type=report_type
                ).group_by(ProjectFinancial.item_name).all()

                session.query(ProjectFinancial).filter_by(
                    project_name=project_name, period=period, report_type=report_type
                ).delete(synchronize_session=False)

                for item_name, total_value in aggregated:
                    session.add(ProjectFinancial(
                        project_name=project_name, period=period,
                        report_type=report_type, item_name=item_name,
                        value=total_value, source_file=file_path,
                        uploaded_at=datetime.now()
                    ))
                session.commit()
                session.expire_all()
        else:
            for report_type in ["资产负债表", "利润表", "现金流量表"]:
                aggregated = session.query(
                    FundFinancial.item_name,
                    func.sum(FundFinancial.value).label('total_value')
                ).filter_by(
                    period=period, report_type=report_type
                ).group_by(FundFinancial.item_name).all()

                session.query(FundFinancial).filter_by(
                    period=period, report_type=report_type
                ).delete(synchronize_session=False)

                for item_name, total_value in aggregated:
                    session.add(FundFinancial(
                        period=period, report_type=report_type,
                        item_name=item_name, value=total_value,
                        source_file=file_path, uploaded_at=datetime.now()
                    ))
                session.commit()
                session.expire_all()

        # Validation for project data
        if data_type == 'project':
            total_assets = get_item_from_db(session, ProjectFinancial, project_name, period, "总资产")
            liabilities = get_item_from_db(session, ProjectFinancial, project_name, period, "负债合计")
            equity = get_item_from_db(session, ProjectFinancial, project_name, period, "所有者权益合计")

            if total_assets is not None and liabilities is not None and equity is not None:
                if abs(total_assets - (liabilities + equity)) > 0.01 * abs(total_assets):
                    warnings_list.append(
                        f"资产负债表不平衡: 总资产({total_assets}) ≠ 负债({liabilities}) + 权益({equity})"
                    )

            # Calculate metrics
            debt_ratio = metrics.calc_debt_ratio(project_name, period, db_path=db_path)
            inv_days = metrics.calc_inventory_days(project_name, period, db_path=db_path)
            rec_days = metrics.calc_receivable_days(project_name, period, db_path=db_path)

            metric_results = {
                "资产负债率": debt_ratio,
                "存货周转天数": inv_days,
                "应收周转天数": rec_days
            }
            for name, val in metric_results.items():
                if val is not None:
                    session.query(ProjectMetric).filter_by(
                        project_name=project_name, period=period, metric_name=name
                    ).delete()
                    session.add(ProjectMetric(
                        project_name=project_name, period=period,
                        metric_name=name, metric_value=val, calculated_at=datetime.now()
                    ))
                    metrics_snapshot[name] = val
            session.commit()
        else:
            dpi = metrics.calc_dpi(period, db_path=db_path)
            tvpi = metrics.calc_tvpi(period, db_path=db_path)
            metric_results = {"DPI": dpi, "TVPI": tvpi}
            for name, val in metric_results.items():
                if val is not None:
                    session.query(FundMetric).filter_by(
                        period=period, metric_name=name
                    ).delete()
                    session.add(FundMetric(
                        period=period, metric_name=name,
                        metric_value=val, calculated_at=datetime.now()
                    ))
                    metrics_snapshot[name] = val
            session.commit()

        # Log
        status = "warning" if warnings_list else "success"
        log = CleanLog(
            file_path=file_path, data_type=data_type, status=status,
            warnings=json.dumps(warnings_list, ensure_ascii=False),
            errors=json.dumps(errors_list, ensure_ascii=False),
            created_at=datetime.now()
        )
        session.add(log)
        session.commit()

        return {
            "status": status, "warnings": warnings_list, "errors": errors_list,
            "metrics": metrics_snapshot, "period": period,
            "records_processed": total_records
        }

    except Exception as e:
        session.rollback()
        errors_list.append(str(e))
        return {"status": "error", "warnings": warnings_list, "errors": errors_list, "metrics": {}}
    finally:
        session.close()
