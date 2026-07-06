import json
from unittest.mock import patch, MagicMock
from core import llm_helper


def test_generate_returns_empty_on_disabled():
    with patch('core.llm_helper.get_llm_client', return_value=None):
        result = llm_helper.generate("test prompt")
        assert result == ""


def test_smart_match_single_parses_json():
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content=json.dumps({"matched": "总资产"})))]

    with patch('core.llm_helper.get_llm_client') as mock_client:
        mock_client.return_value.chat.completions.create.return_value = mock_response
        result = llm_helper.smart_match_single("资产总计", ["总资产", "负债合计"])
        assert result == "总资产"


def test_smart_match_single_returns_none_on_invalid():
    with patch('core.llm_helper.get_llm_client') as mock_client:
        mock_client.return_value.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="not json"))]
        )
        result = llm_helper.smart_match_single("未知", ["总资产"])
        assert result is None


def test_smart_match_batch_parses_dict():
    mock_content = json.dumps({"资产总计": "总资产", "营业收入": "营业收入"})
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content=mock_content))]

    with patch('core.llm_helper.get_llm_client') as mock_client:
        mock_client.return_value.chat.completions.create.return_value = mock_response
        result = llm_helper.smart_match_batch(["资产总计", "营业收入"], ["总资产", "营业收入"])
        assert result == {"资产总计": "总资产", "营业收入": "营业收入"}


def test_explain_error_returns_string():
    with patch('core.llm_helper.generate', return_value="网络超时"):
        result = llm_helper.explain_error("API timeout")
        assert result == "网络超时"
