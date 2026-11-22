"""One sanitized result shared by the model, events, charts and downloads."""

import datetime
import decimal
import math
import re

from .contracts import QueryResult, SourcePolicy


def sanitize(result: QueryResult, policy: SourcePolicy) -> QueryResult:
    masked = {label.lower() for label in policy.masked_labels}

    def cell(value):
        if value is None or isinstance(value, (bool, int)):
            return value
        if isinstance(value, (float, decimal.Decimal)):
            value = float(value)
            return value if math.isfinite(value) else None
        if isinstance(value, (datetime.date, datetime.datetime)):
            return value.isoformat()
        if isinstance(value, bytes):
            return "[binary omitted]"
        return str(value)[:500]

    rows = [
        [
            "***" if name.lower() in masked else cell(value)
            for name, value in zip(result.columns, row)
        ]
        for row in result.rows
    ]
    return QueryResult(result.columns, rows, result.truncated, result.elapsed_ms)


def chart_spec(result: QueryResult):
    """Frontend chart data, never executable model-generated JavaScript."""
    if len(result.columns) != 2 or not result.rows:
        return None
    if not all(type(row[1]) in {int, float} for row in result.rows):
        return None
    dated = len(result.rows) > 1 and all(
        isinstance(row[0], str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", row[0])
        for row in result.rows
    )
    data = sorted(result.rows, key=lambda row: row[0]) if dated else result.rows
    return {
        "type": "line" if dated else "bar",
        "x": result.columns[0],
        "y": result.columns[1],
        "data": data[:50],
    }
