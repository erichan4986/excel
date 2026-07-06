import os
import tempfile
import pytest
from sqlalchemy import inspect
from core.database import (
    Base, init_db, get_session, ProjectFinancial, FundFinancial,
    ProjectMetric, FundMetric, CleanLog
)


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    yield path
    os.unlink(path)


def test_init_db_creates_tables(temp_db):
    init_db(db_path=temp_db)
    engine = get_session(db_path=temp_db).bind
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    assert 'project_financials' in tables
    assert 'fund_financials' in tables
    assert 'project_metrics' in tables
    assert 'fund_metrics' in tables
    assert 'clean_logs' in tables


def test_project_financial_crud(temp_db):
    init_db(db_path=temp_db)
    session = get_session(db_path=temp_db)
    record = ProjectFinancial(
        project_name="项目A",
        period="2024-12-31",
        report_type="资产负债表",
        item_name="总资产",
        value=1000.5,
        source_file="test.xlsx"
    )
    session.add(record)
    session.commit()

    result = session.query(ProjectFinancial).filter_by(project_name="项目A").first()
    assert result is not None
    assert result.value == 1000.5
    session.close()


def test_clean_log_crud(temp_db):
    init_db(db_path=temp_db)
    session = get_session(db_path=temp_db)
    log = CleanLog(
        file_path="test.xlsx",
        data_type="project",
        status="success",
        warnings="[]",
        errors="[]"
    )
    session.add(log)
    session.commit()

    result = session.query(CleanLog).filter_by(file_path="test.xlsx").first()
    assert result.status == "success"
    session.close()
