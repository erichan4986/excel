import json
import os
import pytest
from unittest.mock import patch, MagicMock
from core import chatbot
from core.database import init_db


@pytest.fixture(autouse=True)
def setup_db():
    db_path = "data/fund_data.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    init_db(db_path=db_path)
    yield
    if os.path.exists(db_path):
        os.remove(db_path)


def test_parse_intent_fill_template():
    mock_content = json.dumps({
        "intent": "fill_template",
        "params": {
            "template_path": "test.xlsx",
            "layout": {"company_axis": "row", "metric_axis": "col"},
            "period": "2024-12-31"
        },
        "response": "已理解"
    })
    with patch("core.chatbot.llm_helper.generate", return_value=mock_content):
        result = chatbot.process_message("公司名在第一列，指标在第一行，填2024年的数据")
        assert result["intent"] == "fill_template"
        assert result["params"]["layout"]["company_axis"] == "row"
        assert result["params"]["layout"]["metric_axis"] == "col"


def test_parse_intent_query_data():
    mock_content = json.dumps({
        "intent": "query_data",
        "params": {
            "project_name": "公司A",
            "metric_name": "存货",
            "period_range": "2023-2025"
        },
        "response": "正在查询"
    })
    with patch("core.chatbot.llm_helper.generate", return_value=mock_content):
        result = chatbot.process_message("公司A近三年的存货情况")
        assert result["intent"] == "query_data"
        assert result["params"]["project_name"] == "公司A"


def test_parse_intent_llm_unavailable():
    with patch("core.chatbot.llm_helper.generate", return_value=""):
        result = chatbot.process_message("随便说点什么")
        assert result["intent"] == "general_chat"
        assert "抱歉" in result["response"]


def test_parse_intent_invalid_json():
    with patch("core.chatbot.llm_helper.generate", return_value="这不是JSON"):
        result = chatbot.process_message("测试")
        assert result["intent"] == "general_chat"


def test_fallback_fill_template_with_column_letter():
    """Ensure '填...数据' is recognized as fill_template, not show_data.
    '公司名在C列' → company names down column C (company_axis='row').
    '表头在第一行' → metrics across row 1 (metric_axis='col')."""
    with patch("core.chatbot.llm_helper.generate", return_value=""):
        result = chatbot.process_message("公司名在C列，表头在第一行，填2025数据")
        assert result["intent"] == "fill_template"
        assert result["params"]["period"] == "2025-12-31"
        layout = result["params"]["layout"]
        assert layout["company_axis"] == "row"
        assert layout["metric_axis"] == "col"
        assert layout["company_header_col"] == 3
        assert layout["metric_header_row"] == 1


def test_fallback_list_projects_database_query():
    """Ensure generic database queries don't get misclassified as list_periods."""
    with patch("core.chatbot.llm_helper.generate", return_value=""):
        result = chatbot.process_message("数据库有什么数据")
        assert result["intent"] == "list_projects"
        assert "项目" in result["response"]


def test_execute_list_projects():
    result = chatbot.execute_list_projects()
    assert result["success"]
    assert "projects" in result


def test_execute_list_periods():
    result = chatbot.execute_list_periods({"project_name": "公司A"})
    assert result["success"]
    assert "periods" in result


def test_execute_show_data():
    result = chatbot.execute_show_data({"project_name": "公司A", "period": "2024-12-31"})
    assert result["success"]
    assert "items" in result
    assert "metrics" in result

