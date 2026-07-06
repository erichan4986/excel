import pandas as pd
import pdfplumber


def extract_tables(pdf_path):
    """Extract all tables from a PDF and return as list of DataFrames."""
    tables = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_tables = page.extract_tables()
            for table in page_tables:
                if table and len(table) > 1:
                    headers = table[0]
                    rows = table[1:]
                    df = pd.DataFrame(rows, columns=headers)
                    tables.append(df)
                elif table:
                    df = pd.DataFrame(table)
                    tables.append(df)
    return tables
