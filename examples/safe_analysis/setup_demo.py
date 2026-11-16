"""Create deterministic synthetic data and a restricted view, without overwrites."""

import argparse
import datetime
import sqlite3
from pathlib import Path


def create_demo(path):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS sales_raw ("
            "id INTEGER PRIMARY KEY, sale_date TEXT, region TEXT, amount REAL, "
            "status TEXT, customer_email TEXT, customer_phone TEXT)"
        )
        start = datetime.date(2026, 1, 1)
        conn.executemany(
            "INSERT OR IGNORE INTO sales_raw VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    i,
                    (start + datetime.timedelta(days=i % 90)).isoformat(),
                    ("East", "West", "North", "South")[i % 4],
                    (i % 100 + 1) * 10.0,
                    "cancelled" if i % 10 == 0 else "completed",
                    f"synthetic-{i}@example.invalid",
                    "00000000000",
                )
                for i in range(1, 10001)
            ),
        )
        conn.execute(
            "CREATE VIEW IF NOT EXISTS sales_public AS "
            "SELECT id, sale_date, region, amount, status FROM sales_raw"
        )
        count = conn.execute("SELECT COUNT(*) FROM sales_raw").fetchone()[0]
    print(f"Created/verified {count} synthetic rows and sales_public view.")
    print("Set DATA_AGENT_SALES_URL to sqlite:///" + path.as_posix())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default="data/safe-analysis-demo.sqlite3")
    create_demo(parser.parse_args().path)
