from unittest.mock import patch, MagicMock
import pandas as pd
from core import pdf_processor


def test_extract_tables_mock():
    mock_page = MagicMock()
    mock_page.extract_tables.return_value = [[[1, 2], [3, 4]]]
    mock_pdf = MagicMock()
    mock_pdf.pages = [mock_page]
    mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
    mock_pdf.__exit__ = MagicMock(return_value=False)

    with patch('pdfplumber.open', return_value=mock_pdf):
        tables = pdf_processor.extract_tables("test.pdf")
        assert len(tables) == 1
        assert list(tables[0].columns) == [1, 2]
