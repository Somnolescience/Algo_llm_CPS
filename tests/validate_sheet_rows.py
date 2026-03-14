from __future__ import annotations

import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pdf_table_extractor import extract_first_table, extract_cbo_data, format_bill_input_row

FIXTURE_PATH = Path("tests/fixtures/expected_sheet_rows.tsv")


def render_row(parts: list[str]) -> str:
    return " | ".join(parts)


def validate_expected_rows() -> None:
    with FIXTURE_PATH.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        rows = list(reader)

    if not rows:
        raise AssertionError("Fixture file has no rows to validate.")

    failures: list[str] = []
    for item in rows:
        pdf_path = item["pdf_path"]
        expected = item["expected_row"]

        table = extract_first_table(pdf_path)
        if table is None:
            failures.append(f"{pdf_path}: no table extracted")
            continue

        parsed = extract_cbo_data(table)
        actual = render_row(format_bill_input_row(parsed))

        if actual != expected:
            failures.append(
                f"{pdf_path}:\n  expected: {expected}\n  actual:   {actual}"
            )

    if failures:
        raise AssertionError("\n\n".join(failures))


if __name__ == "__main__":
    validate_expected_rows()
    print(f"Validated {FIXTURE_PATH} successfully.")
