import re
import pdfplumber
import pandas as pd
import sys
import os

VALUE_TOKEN_RE = re.compile(r"^(?:\d{1,3}(?:,\d{3})*|0|a|\*|no|no info)$", re.IGNORECASE)

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
                        parts = line.split()
                        if len(parts) >= 4:
                            data['year_ranges'] = parts[-3:]
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

    return data

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

def main():
    if len(sys.argv) < 2:
        print("Usage: python pdf_table_extractor.py <pdf_path_or_directory> [output_csv]")
        print("If pdf_path is a directory, all PDFs in it will be processed.")
        sys.exit(1)

    pdf_path = sys.argv[1]
    output_csv = sys.argv[2] if len(sys.argv) > 2 else None

    if os.path.isdir(pdf_path):
        # Process all PDFs in directory
        pdf_files = [f for f in os.listdir(pdf_path) if f.lower().endswith('.pdf')]
        if not pdf_files:
            print(f"No PDF files found in directory {pdf_path}")
            sys.exit(1)
        results = []
        for pdf_file in pdf_files:
            full_path = os.path.join(pdf_path, pdf_file)
            print(f"Processing {pdf_file}...")
            result = process_pdf(full_path, None)  # Don't save individual CSVs
            if result:
                result['pdf'] = pdf_file
                results.append(result)
                print(f"Extracted data from {pdf_file}")
        print("All extracted data:")
        for res in results:
            print(res)
        if output_csv:
            # Save all data to CSV
            all_rows = []
            for res in results:
                all_rows.append([res.get('pdf', '')])
                if 'year_ranges' in res:
                    all_rows.append(['Year Ranges'] + res['year_ranges'])
                if 'direct_spending' in res:
                    all_rows.append(['Direct Spending (Outlays)'] + res['direct_spending'])
                # Add other rows as needed
                all_rows.append([])  # Blank row between PDFs
            if all_rows:
                df = pd.DataFrame(all_rows)
                df.to_csv(output_csv, index=False, header=False)
                print(f"All data saved to {output_csv}")
    else:
        # Single PDF
        if not os.path.exists(pdf_path):
            print(f"PDF file {pdf_path} does not exist.")
            sys.exit(1)
        result = process_pdf(pdf_path, output_csv)
        if result:
            print("Extracted data:")
            print(result)

if __name__ == "__main__":
    main()