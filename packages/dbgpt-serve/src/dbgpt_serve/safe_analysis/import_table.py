"""Operator-side CSV/XLSX -> persistent DuckDB; never callable by the model.

Example:
python -m dbgpt_serve.safe_analysis.import_table sales.xlsx data/sales.duckdb sales
"""

import argparse
import json
import re
from pathlib import Path

from .contracts import AnalysisError


def import_table(input_path, database_path, table, sheet=0):
    import duckdb
    import pandas as pd

    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,62}", table):
        raise AnalysisError("导入表名只允许字母、数字、下划线，且不能以数字开头")
    input_path, database_path = (
        Path(input_path).resolve(),
        Path(database_path).resolve(),
    )
    if not input_path.is_file() or input_path.stat().st_size > 100 * 1024 * 1024:
        raise AnalysisError("输入文件不存在或超过 100MB")
    if input_path == database_path:
        raise AnalysisError("输入文件与数据库文件不能相同")
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(database_path)) as db:
        # CREATE TABLE (no REPLACE) deliberately refuses to overwrite user data.
        if input_path.suffix.lower() == ".csv":
            db.execute(
                f'CREATE TABLE "{table}" AS SELECT * FROM read_csv_auto(?)',
                [str(input_path)],
            )
        elif input_path.suffix.lower() == ".xlsx":
            frame = pd.read_excel(input_path, sheet_name=sheet, engine="openpyxl")
            db.register("_import_frame", frame)
            db.execute(f'CREATE TABLE "{table}" AS SELECT * FROM _import_frame')
        else:
            raise AnalysisError("表格导入支持 CSV/XLSX；PDF/Word 是知识文档")
        columns = db.execute(f'DESCRIBE "{table}"').fetchall()
        count = db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    return {
        "url": "duckdb:///" + database_path.as_posix(),
        "dialect": "duckdb",
        "schema": "main",
        "tables": {table: {row[0]: row[1] for row in columns}},
        "imported_rows": count,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("database")
    parser.add_argument("table")
    parser.add_argument("--sheet", default=0)
    args = parser.parse_args()
    print(
        json.dumps(
            import_table(args.input, args.database, args.table, args.sheet),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
