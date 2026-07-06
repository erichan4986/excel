import os
import random
import pandas as pd
from datetime import datetime
from core.paths import SAMPLE_DIR

random.seed(42)

OUTPUT_DIR = SAMPLE_DIR
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

COMPANIES = ["公司A", "公司B", "公司C"]
YEARS = [2023, 2024, 2025]


def generate_balance_sheet(year, prev_year_data=None):
    """生成资产负债表，保持会计恒等式。"""
    # 基础规模随公司略有不同
    scale = random.randint(800, 2000)

    # 资产
    cash = round(scale * random.uniform(0.15, 0.30), 2)
    receivables = round(scale * random.uniform(0.10, 0.25), 2)
    inventory = round(scale * random.uniform(0.08, 0.20), 2)
    other_current = round(scale * random.uniform(0.02, 0.08), 2)
    current_assets = round(cash + receivables + inventory + other_current, 2)

    fixed_assets = round(scale * random.uniform(0.30, 0.55), 2)
    intangible = round(scale * random.uniform(0.05, 0.15), 2)
    other_noncurrent = round(scale * random.uniform(0.02, 0.10), 2)
    non_current_assets = round(fixed_assets + intangible + other_noncurrent, 2)

    total_assets = round(current_assets + non_current_assets, 2)

    # 负债
    short_loan = round(scale * random.uniform(0.05, 0.15), 2)
    payables = round(scale * random.uniform(0.08, 0.18), 2)
    other_current_liab = round(scale * random.uniform(0.02, 0.08), 2)
    current_liabilities = round(short_loan + payables + other_current_liab, 2)

    long_loan = round(scale * random.uniform(0.10, 0.25), 2)
    other_noncurrent_liab = round(scale * random.uniform(0.02, 0.08), 2)
    non_current_liabilities = round(long_loan + other_noncurrent_liab, 2)

    total_liabilities = round(current_liabilities + non_current_liabilities, 2)

    # 权益（倒挤）
    equity = round(total_assets - total_liabilities, 2)
    paid_in_capital = round(scale * random.uniform(0.20, 0.35), 2)
    capital_reserve = round(scale * random.uniform(0.05, 0.15), 2)
    # 未分配利润作为差额项
    retained_earnings = round(equity - paid_in_capital - capital_reserve, 2)

    data = {
        "项目": [
            "货币资金", "应收账款", "存货", "其他流动资产",
            "流动资产合计",
            "固定资产", "无形资产", "其他非流动资产",
            "非流动资产合计",
            "总资产",
            "短期借款", "应付账款", "其他流动负债",
            "流动负债合计",
            "长期借款", "其他非流动负债",
            "非流动负债合计",
            "负债合计",
            "实收资本", "资本公积", "未分配利润",
            "所有者权益合计",
        ],
        "期末余额": [
            cash, receivables, inventory, other_current,
            current_assets,
            fixed_assets, intangible, other_noncurrent,
            non_current_assets,
            total_assets,
            short_loan, payables, other_current_liab,
            current_liabilities,
            long_loan, other_noncurrent_liab,
            non_current_liabilities,
            total_liabilities,
            paid_in_capital, capital_reserve, retained_earnings,
            equity,
        ]
    }
    return pd.DataFrame(data)


def generate_income_statement(year, balance_sheet_df):
    """生成利润表，与资产负债表中的未分配利润保持大致关系。"""
    total_assets = balance_sheet_df[balance_sheet_df["项目"] == "总资产"]["期末余额"].values[0]
    scale = total_assets * random.uniform(0.4, 0.8)

    revenue = round(scale * random.uniform(0.8, 1.2), 2)
    cost = round(revenue * random.uniform(0.55, 0.75), 2)
    gross_profit = round(revenue - cost, 2)

    sales_expense = round(revenue * random.uniform(0.03, 0.08), 2)
    admin_expense = round(revenue * random.uniform(0.05, 0.12), 2)
    finance_expense = round(revenue * random.uniform(0.01, 0.04), 2)

    operating_profit = round(gross_profit - sales_expense - admin_expense - finance_expense, 2)
    tax = round(operating_profit * random.uniform(0.15, 0.25), 2) if operating_profit > 0 else 0
    net_profit = round(operating_profit - tax, 2)

    data = {
        "项目": [
            "营业收入", "营业成本", "毛利",
            "销售费用", "管理费用", "财务费用",
            "营业利润", "所得税费用", "净利润"
        ],
        "本期金额": [
            revenue, cost, gross_profit,
            sales_expense, admin_expense, finance_expense,
            operating_profit, tax, net_profit
        ]
    }
    return pd.DataFrame(data)


def generate_cashflow_statement(year, balance_sheet_df, income_df):
    """生成现金流量表，保持三大活动逻辑。"""
    net_profit = income_df[income_df["项目"] == "净利润"]["本期金额"].values[0]

    # 经营现金流 ≈ 净利润 + 折旧等非现金项
    depreciation = round(balance_sheet_df[balance_sheet_df["项目"] == "固定资产"]["期末余额"].values[0] * 0.05, 2)
    operating_cf = round(net_profit + depreciation + random.uniform(-50, 100), 2)

    # 投资现金流（通常为负）
    investing_cf = round(random.uniform(-300, -50), 2)

    # 筹资现金流
    financing_cf = round(random.uniform(-100, 200), 2)

    net_increase = round(operating_cf + investing_cf + financing_cf, 2)

    data = {
        "项目": [
            "经营活动产生的现金流量净额",
            "投资活动产生的现金流量净额",
            "筹资活动产生的现金流量净额",
            "现金及现金等价物净增加额",
        ],
        "本期金额": [
            operating_cf, investing_cf, financing_cf, net_increase
        ]
    }
    return pd.DataFrame(data)


def main():
    for company in COMPANIES:
        for year in YEARS:
            # 文件名包含期间信息，方便系统识别
            filename = f"{company}_{year}-12-31.xlsx"
            filepath = OUTPUT_DIR / filename

            bs = generate_balance_sheet(year)
            income = generate_income_statement(year, bs)
            cf = generate_cashflow_statement(year, bs, income)

            with pd.ExcelWriter(str(filepath), engine='openpyxl') as writer:
                bs.to_excel(writer, sheet_name='资产负债表', index=False)
                income.to_excel(writer, sheet_name='利润表', index=False)
                cf.to_excel(writer, sheet_name='现金流量表', index=False)

            print(f"Generated: {filepath}")

    print(f"\nDone! {len(COMPANIES) * len(YEARS)} files saved to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
