import os
from pathlib import Path
import pandas as pd
import pytest
from core.database import init_db, get_session, ProjectFinancial, CleanLog
from core import cleaner, fund_parser

TEST_DB = "data/test_cleaner.db"
UPLOAD_DIR = "data/uploads"


@pytest.fixture(autouse=True)
def setup():
    for p in [TEST_DB]:
        if os.path.exists(p):
            os.remove(p)
    init_db(db_path=TEST_DB)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    yield
    for p in [TEST_DB]:
        if os.path.exists(p):
            os.remove(p)


def make_test_excel(path, sheet_name="资产负债表"):
    df = pd.DataFrame({
        "项目": ["总资产", "负债合计", "所有者权益合计", "存货", "营业成本", "应收账款", "营业收入"],
        "期末余额": [1000.0, 400.0, 600.0, 100.0, 200.0, 150.0, 500.0]
    })
    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False)


def test_clean_and_store_project():
    path = os.path.join(UPLOAD_DIR, "项目A_2024-12-31.xlsx")
    make_test_excel(path)
    result = cleaner.clean_and_store(path, "project", db_path=TEST_DB)

    assert result["status"] == "success"
    assert len(result["warnings"]) == 0
    assert "资产负债率" in result["metrics"]

    session = get_session(db_path=TEST_DB)
    records = session.query(ProjectFinancial).filter_by(project_name="项目A", period="2024-12-31").all()
    assert len(records) == 7
    session.close()


def test_balance_sheet_validation_warning():
    path = os.path.join(UPLOAD_DIR, "项目B_2024-12-31.xlsx")
    df = pd.DataFrame({
        "项目": ["总资产", "负债合计", "所有者权益合计"],
        "期末余额": [1000.0, 400.0, 500.0]  # 400+500 != 1000
    })
    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name="资产负债表", index=False)

    result = cleaner.clean_and_store(path, "project", db_path=TEST_DB)
    assert result["status"] == "warning"
    assert any("不平衡" in w for w in result["warnings"])


def test_csv_snapshot_created():
    path = os.path.join(UPLOAD_DIR, "项目C_2024-06-30.xlsx")
    make_test_excel(path, "利润表")
    cleaner.clean_and_store(path, "project", db_path=TEST_DB)
    assert os.path.exists("data/cleaned/项目C_2024-06-30.xlsx_2024-06-30_cleaned.csv")


TEST_DB2 = "data/test_cleaner2.db"


@pytest.fixture
def setup_duplicate():
    for p in [TEST_DB2]:
        if os.path.exists(p):
            os.remove(p)
    init_db(db_path=TEST_DB2)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    session = get_session(db_path=TEST_DB2)
    session.add(ProjectFinancial(
        project_name="公司A", period="2024-12-31",
        report_type="BS", item_name="总资产", value=999.0,
        source_file="old.xlsx"
    ))
    session.commit()
    session.close()
    yield TEST_DB2
    for p in [TEST_DB2]:
        if os.path.exists(p):
            os.remove(p)


def test_clean_overwrite_false_returns_confirm(setup_duplicate):
    df = pd.DataFrame({"项目": ["总资产"], "期末余额": [1000.0]})
    path = os.path.join(UPLOAD_DIR, "公司A_2024-12-31.xlsx")
    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name="资产负债表", index=False)
    result = cleaner.clean_and_store(path, "project", db_path=setup_duplicate, overwrite=False)
    assert result["status"] == "confirm"
    assert len(result.get("duplicates", [])) > 0
    assert result["duplicates"][0]["period"] == "2024-12-31"


def test_clean_overwrite_true_replaces(setup_duplicate):
    df = pd.DataFrame({"项目": ["总资产"], "期末余额": [2000.0]})
    path = os.path.join(UPLOAD_DIR, "公司A_2024-12-31.xlsx")
    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name="资产负债表", index=False)
    result = cleaner.clean_and_store(path, "project", db_path=setup_duplicate, overwrite=True)
    assert result["status"] == "success"
    session = get_session(db_path=setup_duplicate)
    rec = session.query(ProjectFinancial).filter_by(
        project_name="公司A", period="2024-12-31", item_name="总资产"
    ).first()
    session.close()
    assert rec.value == 2000.0


def test_clean_default_overwrite_is_false(setup_duplicate):
    df = pd.DataFrame({"项目": ["总资产"], "期末余额": [1000.0]})
    path = os.path.join(UPLOAD_DIR, "公司A_2024-12-31.xlsx")
    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name="资产负债表", index=False)
    # Default overwrite should be False (backward compatible)
    result = cleaner.clean_and_store(path, "project", db_path=setup_duplicate)
    assert result["status"] == "confirm"


def test_parse_repo_fund_metric_fixture():
    """The checked-in multi-fund fixture remains compatible with the parser."""
    repo_root = Path(__file__).resolve().parents[2]
    fixture = repo_root / "项目指标数据_财务部提供_20260331_测试.xlsx"

    period, records, warnings = fund_parser.parse_fund_fair_value(
        str(fixture), original_filename=fixture.name
    )

    assert period == "2026-03-31"
    assert len(records) == 22
    assert {record["fund_name"] for record in records} == {"并购一期", "浦江一期", "并购三期"}
    assert not warnings


def test_reject_lp_update_template_as_fund_source():
    """A destination template must not be silently ingested as fund metrics."""
    template = Path(__file__).resolve().parents[1] / "基金项目2025Q4更新.xlsx"

    period, records, warnings = fund_parser.parse_fund_fair_value(
        str(template), original_filename=template.name, period_override="2025-12-31"
    )

    assert period == "2025-12-31"
    assert not records
    assert any("缺少基金项目指标必填列" in warning for warning in warnings)
