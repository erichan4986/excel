from unittest.mock import patch
from core import matcher


def test_exact_match():
    result = matcher.match_header("总资产", use_llm=False)
    assert result == "总资产"


def test_alias_match():
    result = matcher.match_header("资产总计", use_llm=False)
    assert result == "总资产"


def test_no_match_returns_none():
    result = matcher.match_header("完全不相关的东西", use_llm=False)
    assert result is None


def test_llm_fallback():
    with patch('core.llm_helper.smart_match_single', return_value="总资产"):
        result = matcher.match_header("资产合计数", use_llm=True)
        assert result == "总资产"


def test_batch_match():
    with patch('core.llm_helper.smart_match_batch', return_value={"资产总计": "总资产"}):
        result = matcher.batch_match(["资产总计"])
        assert result == {"资产总计": "总资产"}
