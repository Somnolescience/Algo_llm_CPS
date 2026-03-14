import re
import pdfplumber
import pandas as pd
import sys
import os
import argparse

VALUE_TOKEN_RE = re.compile(r"^(?:\d{1,3}(?:,\d{3})*|0|a|\*|no|no info)$", re.IGNORECASE)
BUDGET_RANGE_RE = re.compile(r"^\d{4}(?:-\d{4})?$")

def is_value_token(tok: str) -> bool:
    """Return True if the token looks like a valid fiscal table value."""
    if not tok or not isinstance(tok, str):
        return False
    return bool(VALUE_TOKEN_RE.match(tok.strip()))

def extract_first_table(pdf_path):
    """
    Extract the first table from a PDF file, trying different settings for different formats.

    Args:
        pdf_path (str): Path to the PDF file.

    Returns:
        list: A list of lists representing the table, or None if no table found.
    """
    try:
        table_settings_options = [
            {},  # Default
            {"vertical_strategy": "lines", "horizontal_strategy": "lines"},
            {"vertical_strategy": "text", "horizontal_strategy": "text"},
            {"snap_tolerance": 3},
            {"join_tolerance": 3},
        ]

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                for settings in table_settings_options:
                    tables = page.extract_tables(table_settings=settings)
                    if tables:
                        # Return the first table found
                        return tables[0]
    except Exception as e:
        print(f"Error processing {pdf_path}: {e}")
        return None
    return None

def restructure_cbo_table(table):
    """
    Restructure the extracted table for CBO cost estimate format.
    Handles both single cell with multi-line text and multi-row tables.
    """
    if not table:
        return table

    # Find the cell containing "By Fiscal Year"
    text = None
    for row in table:
        for cell in row:
            if cell and isinstance(cell, str) and 'By Fiscal Year' in cell:
                text = cell
                break
        if text:
            break

    if not text:
        return table

    lines = [line.strip() for line in text.split('\n') if line.strip()]

    if not lines:
        return table

    # Parse header
    header_line = lines[0]
    parts = header_line.split()
    if len(parts) < 4:
        return table
    years = parts[-3:]
    label = ' '.join(parts[:-3])
    header = [label] + years

    # Parse data rows
    data = []
    current_label = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 4 and all(is_value_token(p) for p in parts[-3:]):
            # Line has label and 3 values
            label_str = ' '.join(parts[:-3])
            nums = parts[-3:]
            data.append([label_str, nums[0], nums[1], nums[2]])
            current_label = []
        elif len(parts) == 3 and all(is_value_token(p) for p in parts):
            # Line has 3 values (continued label)
            if current_label:
                label_str = ' '.join(current_label)
                data.append([label_str, parts[0], parts[1], parts[2]])
                current_label = []
        else:
            # Label part
            if current_label:
                current_label.extend(parts)
            else:
                current_label = parts

    # If there's remaining label, append to last data row
    if current_label and data:
        data[-1][0] += ' ' + ' '.join(current_label)

    return [header] + data

def parse_table_rows(text):
    """
    Parse the table text into rows with exactly four elements each.
    Elements are created starting from the end: last three are separate, rest is one.
    """
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    table = []
    for line in lines:
        parts = line.split()
        if len(parts) >= 3:
            elem1 = ' '.join(parts[:-3]) if len(parts) > 3 else ''
            elem2 = parts[-3]
            elem3 = parts[-2]
            elem4 = parts[-1]
            table.append([elem1, elem2, elem3, elem4])
    return table

def extract_budget_tokens_from_header(header_line: str) -> list[str]:
    """Extract budget range tokens (e.g. 2026, 2026-2030, 2026-2035) from a header line."""
    parts = header_line.split()
    return [part for part in parts if BUDGET_RANGE_RE.match(part)]


def parse_budget_window(window_label: str) -> int | None:
    """Return window length in years for labels like 2026-2030; None for non-range labels."""
    if not window_label or '-' not in window_label:
        return None
    try:
        start_year, end_year = window_label.split('-', 1)
        return int(end_year) - int(start_year) + 1
    except ValueError:
        return None


def normalize_extracted_data(data: dict) -> dict:
    """Populate explicit 5-year and 10-year fields from header-aligned values."""
    year_ranges = data.get('year_ranges', [])
    if not isinstance(year_ranges, list):
        year_ranges = []

    window_5y = None
    window_10y = None
    for budget_range in year_ranges:
        span = parse_budget_window(budget_range)
        if span == 5 and window_5y is None:
            window_5y = budget_range
        elif span == 10 and window_10y is None:
            window_10y = budget_range

    if window_10y is None and year_ranges:
        window_10y = year_ranges[-1]
    if window_5y is None and year_ranges:
        for budget_range in year_ranges:
            if budget_range != window_10y and '-' in budget_range:
                window_5y = budget_range
                break

    if window_5y:
        data['window_5y'] = window_5y
    if window_10y:
        data['window_10y'] = window_10y

    metric_keys = ['direct_spending', 'revenues', 'deficit_change', 'spending_appropriation']
    for key in metric_keys:
        values = data.get(key, [])
        if not isinstance(values, list) or not values:
            continue

        range_to_value = {}
        for idx, budget_range in enumerate(year_ranges):
            if idx < len(values):
                range_to_value[budget_range] = values[idx]

        if window_5y and window_5y in range_to_value:
            data[f'{key}_5y'] = range_to_value[window_5y]
        if window_10y and window_10y in range_to_value:
            data[f'{key}_10y'] = range_to_value[window_10y]

        if f'{key}_10y' not in data:
            data[f'{key}_10y'] = values[-1]
        if f'{key}_5y' not in data and values:
            data[f'{key}_5y'] = values[0]

    return data


def extract_cbo_data(table):
    """
    Extract specific CBO data from the table.
    """
    data = {}

    # Find bill
    for row in table:
        for cell in row:
            if cell and isinstance(cell, str) and cell.startswith('S. ') and ',' in cell:
                data['bill'] = cell
                break
        if 'bill' in data:
            break

    # Find fiscal data from the "By Fiscal Year" cell
    for row in table:
        for cell in row:
            if cell and isinstance(cell, str) and 'By Fiscal Year' in cell:
                lines = cell.split('\n')
                # Parse header
                for line in lines:
                    if 'By Fiscal Year' in line:
                        budget_tokens = extract_budget_tokens_from_header(line)
                        if budget_tokens:
                            data['year_ranges'] = budget_tokens[-3:]
                        break
                # Parse data lines with multi-line support
                current_label = []
                for line in lines[1:]:
                    parts = line.split()
                    if len(parts) >= 4 and all(is_value_token(p) for p in parts[-3:]):
                        # Label with values
                        label = ' '.join(parts[:-3])
                        nums = parts[-3:]
                        full_label = (' '.join(current_label) + ' ' + label).strip() if current_label else label
                        if 'Direct Spending' in full_label:
                            data['direct_spending'] = nums
                        elif 'Revenues' in full_label:
                            data['revenues'] = nums
                        elif 'Increase or Decrease' in full_label:
                            data['deficit_change'] = nums
                        current_label = []
                    elif len(parts) == 3 and all(is_value_token(p) for p in parts):
                        # Values for current_label
                        full_label = ' '.join(current_label) if current_label else ''
                        if 'Increase or Decrease' in full_label:
                            data['deficit_change'] = parts
                        current_label = []
                    else:
                        # Label part
                        if current_label:
                            current_label.extend(parts)
                        else:
                            current_label = parts
                break

    # For spending_appropriation, search across table rows
    for row in table:
        if any('Spending Subject to' in str(cell) for cell in row):
            # Collect tokens and normalize "not estimated" into one token
            tokens = [str(cell).strip() for cell in row if cell and str(cell).strip()]
            # Find start of label and keep values after it
            start = 0
            for i, t in enumerate(tokens):
                if 'Spending Subject to' in t:
                    start = i + 1
                    break
            vals = tokens[start:]

            # Merge "not" + "estimated" into "not estimated" if present
            merged = []
            i = 0
            while i < len(vals):
                if i + 1 < len(vals) and vals[i].lower() == 'not' and vals[i+1].lower() == 'estimated':
                    merged.append('not estimated')
                    i += 2
                else:
                    merged.append(vals[i])
                    i += 1

            # Prefer tokens that look like numeric/known value tokens
            candidates = [t for t in merged if is_value_token(t) or t.lower() == 'not estimated']
            if len(candidates) >= 3:
                data['spending_appropriation'] = candidates[:3]
            else:
                # Fallback: take first three remaining tokens
                data['spending_appropriation'] = merged[:3]
            break

    # Mandate effects
    for r, row in enumerate(table):
        for cell in row:
            if cell and 'intergovernmental' in str(cell):
                mandate_cells = []
                # Same row after label
                idx = row.index(cell)
                for c in row[idx+1:]:
                    cell_content = str(c).strip().replace('\n', ' ')
                    if cell_content.startswith('Yes') or (cell_content.startswith('No') and cell_content != 'None'):
                        mandate_cells.append(cell_content)
                # Subsequent rows until private
                for rr in range(r+1, len(table)):
                    row_has_private = any('private-sector' in str(c) for c in table[rr])
                    if row_has_private:
                        break
                    for cc in range(len(table[rr])):
                        cell_content = str(table[rr][cc]).strip().replace('\n', ' ')
                        if cell_content.startswith('Yes') or (cell_content.startswith('No') and cell_content != 'None'):
                            mandate_cells.append(cell_content)
                data['mandate_intergovernmental'] = ' '.join(mandate_cells) if mandate_cells else 'No'
            if cell and 'private-sector' in str(cell):
                mandate_cells = []
                # Same row after label
                idx = row.index(cell)
                for c in row[idx+1:]:
                    cell_content = str(c).strip().replace('\n', ' ')
                    if cell_content.startswith('Yes') or (cell_content.startswith('No') and cell_content != 'None'):
                        mandate_cells.append(cell_content)
                # Subsequent rows
                for rr in range(r+1, len(table)):
                    for cc in range(len(table[rr])):
                        cell_content = str(table[rr][cc]).strip().replace('\n', ' ')
                        if cell_content.startswith('Yes') or (cell_content.startswith('No') and cell_content != 'None'):
                            mandate_cells.append(cell_content)
                data['mandate_private'] = ' '.join(mandate_cells) if mandate_cells else 'No'

    # Notes
    for row in table:
        for cell in row:
            if cell and '* =' in str(cell):
                data['notes'] = cell

    return normalize_extracted_data(data)


def format_money_token(tok: str) -> str:
    """Format fiscal value tokens for sheet output using a single project convention."""
    if tok is None:
        return "*MISSING*"

    token = str(tok).strip()
    if not token:
        return "*MISSING*"

    if token == '*':
        return '*$'

    if token.lower() == 'not estimated':
        return token

    cleaned = token.replace(',', '')
    if re.match(r"^\d+$", cleaned):
        return f"${float(cleaned):.2f}"

    return token


def format_currency_value(value):
    """Backward-compatible wrapper around format_money_token."""
    return format_money_token(value)


def get_last_value(res, key):
    """Get the last token from a list-valued extraction field."""
    values = res.get(key, [])
    if isinstance(values, list) and values:
        return values[-1]
    return None


def is_numeric_or_star_value(value) -> bool:
    """Return True for integer-like tokens (including 0) or an asterisk marker."""
    if value is None:
        return False
    token = str(value).strip()
    if token == '*':
        return True
    return bool(re.match(r"^\d{1,3}(?:,\d{3})*$|^0$", token))


def compute_mandate_flag(res: dict) -> str:
    """Return Yes when either mandate field indicates Yes, otherwise No."""
    intergov = str(res.get('mandate_intergovernmental', ''))
    private = str(res.get('mandate_private', ''))
    return 'Yes' if ('Yes' in intergov or 'Yes' in private) else 'No'


# Backward-compatible alias
compute_mandates_flag = compute_mandate_flag


def format_ssta_for_sheet(res: dict) -> str:
    """Format SStA value using sheet-specific 10-year / 5-year fallback rules."""
    ten_year_value = res.get('spending_appropriation_10y')
    five_year_value = res.get('spending_appropriation_5y')
    five_year_range = res.get('window_5y', '*MISSING 5YR RANGE*')

    if ten_year_value is None:
        return '*MISSING SPENDING SUBJECT TO APPROPRIATION*'

    if is_numeric_or_star_value(ten_year_value):
        return format_money_token(ten_year_value)

    if isinstance(ten_year_value, str) and ten_year_value.lower() == 'not estimated':
        if is_numeric_or_star_value(five_year_value):
            return f"not estimated ({five_year_value}$ {five_year_range})"
        return 'not estimated'

    return 'not estimated'


def format_bill_input_row(res: dict) -> list[str]:
    """Format extracted fields in spreadsheet column order for direct pasting."""
    ten_year_range = res.get('window_10y') or get_last_value(res, 'year_ranges') or '*MISSING YEAR RANGE*'
    direct_spending = format_money_token(res.get('direct_spending_10y'))
    revenues = format_money_token(res.get('revenues_10y'))
    deficit_change = format_money_token(res.get('deficit_change_10y'))
    ssta = format_ssta_for_sheet(res)
    mandates = compute_mandate_flag(res)

    return [ten_year_range, direct_spending, revenues, deficit_change, ssta, mandates]


def print_spreadsheet_row(res):
    """Print one tab-delimited spreadsheet row for direct paste into sheets."""
    print("\t".join(format_bill_input_row(res)))

def process_pdf(pdf_path, output_csv=None):
    """
    Process a single PDF: extract data and optionally save to CSV.

    Args:
        pdf_path (str): Path to the PDF.
        output_csv (str): Optional path to save CSV.
    """
    table = extract_first_table(pdf_path)
    if table is None:
        print(f"No table found in {pdf_path}")
        return None

    data = extract_cbo_data(table)

    if output_csv:
        # Save the fiscal data to CSV
        rows = []
        if 'year_ranges' in data:
            rows.append(['Year Ranges'] + data['year_ranges'])
        if 'direct_spending' in data:
            rows.append(['Direct Spending (Outlays)'] + data['direct_spending'])
        if 'revenues' in data:
            rows.append(['Revenues'] + data['revenues'])
        if 'deficit_change' in data:
            rows.append(['Increase or Decrease (-) in the Deficit'] + data['deficit_change'])
        if 'spending_appropriation' in data:
            rows.append(['Spending Subject to Appropriation (Outlays)'] + data['spending_appropriation'])
        if rows:
            df = pd.DataFrame(rows)
            df.to_csv(output_csv, index=False, header=False)
            print(f"Data saved to {output_csv}")

    return data



def get_default_output_csv(pdf_path: str) -> str:
    """Return a default CSV path written at run termination when none is supplied."""
    base_name = os.path.basename(os.path.normpath(pdf_path))
    if os.path.isdir(pdf_path):
        stem = base_name or 'pdf_directory'
    else:
        stem = os.path.splitext(base_name)[0] or 'pdf_extract'
    return f"{stem}_bill_input_rows.csv"

def write_csv_data(results, output_csv):
    if not results:
        print("No data to save.")
        return

    all_rows = []
    all_rows.append(["BILL", "Budget Window", "Direct Spending (Outlays)", "Revenues", "Total Effect (Increase or (-)Decrease to Deficit", "Spending Subject to Appropriation (Outlays)", "Mandates", "Fiscal Category", "PDF"])
    for res in results:
        bill_info = [res.get('bill', '*MISSING BILL*')] + format_bill_input_row(res)
        bill_info.append(res.get('notes', ''))
        bill_info.append(res.get('pdf', '*MISSING PDF NAME*'))
        all_rows.append(bill_info)

    if output_csv and all_rows:
        df = pd.DataFrame(all_rows)
        df.to_csv(output_csv, index=False, header=False)
        print(f"All data saved to {output_csv}")

def main():
    parser = argparse.ArgumentParser(
        description="Extract CBO table fields from one PDF or a directory of PDFs."
    )
    parser.add_argument("pdf_path", help="Path to a PDF file or directory containing PDFs.")
    parser.add_argument("output_csv", nargs="?", default=None, help="Optional CSV output path.")
    parser.add_argument(
        "--spreadsheet-row",
        action="store_true",
        help="Print tab-delimited rows that can be pasted directly into Google Sheets.",
    )

    args = parser.parse_args()
    pdf_path = args.pdf_path
    output_csv = args.output_csv or get_default_output_csv(pdf_path)
    spreadsheet_row = args.spreadsheet_row
    result = None
    results = []

    if os.path.isdir(pdf_path):
        # Process all PDFs in directory
        pdf_files = [f for f in os.listdir(pdf_path) if f.lower().endswith('.pdf')]
        if not pdf_files:
            print(f"No PDF files found in directory {pdf_path}")
            sys.exit(1)
        for pdf_file in pdf_files:
            full_path = os.path.join(pdf_path, pdf_file)
            print(f"Processing {pdf_file}...")
            result = process_pdf(full_path, None)  # Don't save individual CSVs
            if result:
                result['pdf'] = pdf_file
                results.append(result)
                print(f"Extracted data from {pdf_file}")
                if spreadsheet_row:
                    print_spreadsheet_row(result)
        print("All extracted data:")
        for res in results:
            print(res)
        #if output_csv:
        #    # Save all data to CSV
        #    all_rows = []
        #    for res in results:
        #        all_rows.append([res.get('pdf', '')])
        #        if 'year_ranges' in res:
        #            all_rows.append(['Year Ranges'] + res['year_ranges'])
        #        if 'direct_spending' in res:
        #            all_rows.append(['Direct Spending (Outlays)'] + res['direct_spending'])
        #        # Add other rows as needed
        #        all_rows.append([])  # Blank row between PDFs
        #    if all_rows:
        #        df = pd.DataFrame(all_rows)
        #        df.to_csv(output_csv, index=False, header=False)
        #        print(f"All data saved to {output_csv}")
    else:
        # Single PDF
        if not os.path.exists(pdf_path):
            print(f"PDF file {pdf_path} does not exist.")
            sys.exit(1)
        result = process_pdf(pdf_path)
        if result:
            print("Extracted data:")
            print(result)
            if spreadsheet_row:
                print_spreadsheet_row(result)
    final_results = results if os.path.isdir(pdf_path) else ([result] if result else [])
    write_csv_data(final_results, output_csv)
    print(f"Spreadsheet CSV written to {output_csv}")


if __name__ == "__main__":
    main()
