import os
import pandas as pd
from pathlib import Path
from core.database import get_session, FundFairValue
from core.paths import OUTPUT_DIR


def export_fund_report(fund_name: str, period: str, output_dir = OUTPUT_DIR) -> str:
    """Export fund fair value data to an Excel file."""
    session = get_session()
    try:
        items = session.query(FundFairValue).filter_by(
            fund_name=fund_name, period=period
        ).order_by(FundFairValue.id).all()
    finally:
        session.close()

    if not items:
        raise ValueError(f'No data found for {fund_name} {period}')

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_fund = fund_name.replace('/', '_').replace('\\', '_')
    output_path = output_dir / f'{safe_fund}_{period}_基金公允价值.xlsx'

    data = []
    for i in items:
        data.append({
            '基金': i.fund_name,
            '项目': i.project_name,
            '剩余投资成本': i.cost,
            '项目公允价值': i.fair_value,
            '累计退出回收资金': i.total_return,
            '备注': i.remark or '',
            '最后回款日期': i.last_payment_date or '',
        })

    df = pd.DataFrame(data)

    with pd.ExcelWriter(str(output_path), engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='基金公允价值', index=False)
        worksheet = writer.sheets['基金公允价值']
        # Auto-adjust column widths
        for column in worksheet.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if cell.value:
                        max_length = max(max_length, len(str(cell.value)))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            worksheet.column_dimensions[column_letter].width = adjusted_width

    return str(output_path)
