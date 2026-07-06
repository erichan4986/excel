import os
import pytest
from core.database import init_db, get_session, ProjectFinancial, FundFinancial
from core import metrics

TEST_DB = "data/test_metrics.db"


@pytest.fixture(autouse=True)
def setup_db():
    for p in [TEST_DB]:
        if os.path.exists(p):
            os.remove(p)
    init_db(db_path=TEST_DB)
    yield
    for p in [TEST_DB]:
        if os.path.exists(p):
            os.remove(p)


def seed_project_data(session, project_name, period):
    items = [
        ("总资产", 1000.0),
        ("负债合计", 400.0),
        ("所有者权益合计", 600.0),
        ("存货", 100.0),
        ("营业成本", 200.0),
        ("应收账款", 150.0),
        ("营业收入", 500.0),
    ]
    for item_name, value in items:
        session.add(ProjectFinancial(
            project_name=project_name, period=period,
            report_type="资产负债表", item_name=item_name, value=value,
            source_file="test.xlsx"
        ))
    session.commit()


def seed_fund_data(session, period):
    items = [
        ("累计已分配", 300.0),
        ("实缴资本", 1000.0),
        ("未实现价值", 2000.0),
    ]
    for item_name, value in items:
        session.add(FundFinancial(
            period=period, report_type="基金指标",
            item_name=item_name, value=value, source_file="test.xlsx"
        ))
    session.commit()


def test_calc_debt_ratio():
    session = get_session(db_path=TEST_DB)
    seed_project_data(session, "项目A", "2024-12-31")
    result = metrics.calc_debt_ratio("项目A", "2024-12-31", db_path=TEST_DB)
    assert result == pytest.approx(0.4, rel=1e-3)
    session.close()


def test_calc_inventory_days():
    session = get_session(db_path=TEST_DB)
    seed_project_data(session, "项目A", "2024-12-31")
    result = metrics.calc_inventory_days("项目A", "2024-12-31", db_path=TEST_DB)
    assert result == pytest.approx(45.0, rel=1e-3)
    session.close()


def test_calc_receivable_days():
    session = get_session(db_path=TEST_DB)
    seed_project_data(session, "项目A", "2024-12-31")
    result = metrics.calc_receivable_days("项目A", "2024-12-31", db_path=TEST_DB)
    assert result == pytest.approx(27.0, rel=1e-3)
    session.close()


def test_calc_dpi():
    session = get_session(db_path=TEST_DB)
    seed_fund_data(session, "2024-12-31")
    result = metrics.calc_dpi("2024-12-31", db_path=TEST_DB)
    assert result == pytest.approx(0.3, rel=1e-3)
    session.close()


def test_calc_tvpi():
    session = get_session(db_path=TEST_DB)
    seed_fund_data(session, "2024-12-31")
    result = metrics.calc_tvpi("2024-12-31", db_path=TEST_DB)
    assert result == pytest.approx(2.3, rel=1e-3)
    session.close()


def test_missing_data_returns_none():
    result = metrics.calc_debt_ratio("不存在", "2099-12-31", db_path=TEST_DB)
    assert result is None
