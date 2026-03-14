import pdfplumber
import pandas as pd
import sys
import os

def extract_first_table(pdf_path):
    """
    Extract the first table from a PDF file, trying different settings for different formats.

    Args:
        pdf_path (str): Path to the PDF file.

    Returns:
        list: A list of lists representing the table, or None if no table found.
    """
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
    return None

def table_to_dict(table):
    """
    Convert a table (list of lists) to a dictionary if it has headers.

    Args:
        table (list): List of lists.

    Returns:
        dict: Dictionary with headers as keys, or the original table if no headers.
    """
    if not table or len(table) < 2:
        return table
    headers = table[0]
    data = table[1:]
    # Assume headers are strings
    if all(isinstance(h, str) for h in headers):
        return [dict(zip(headers, row)) for row in data]
    else:
        return table

def process_pdf(pdf_path, output_csv=None):
    """
    Process a single PDF: extract first table and optionally save to CSV.

    Args:
        pdf_path (str): Path to the PDF.
        output_csv (str): Optional path to save CSV.
    """
    table = extract_first_table(pdf_path)
    if table is None:
        print(f"No table found in {pdf_path}")
        return None

    # Convert to dict if possible
    table_dict = table_to_dict(table)

    if output_csv:
        # Save to CSV
        df = pd.DataFrame(table)
        df.to_csv(output_csv, index=False)
        print(f"Table extracted and saved to {output_csv}")

    return table_dict

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
        for pdf_file in pdf_files:
            full_path = os.path.join(pdf_path, pdf_file)
            print(f"Processing {pdf_file}...")
            result = process_pdf(full_path, None)  # Don't save individual CSVs
            if result:
                print(f"Extracted table from {pdf_file}:")
                if isinstance(result, list) and result and isinstance(result[0], dict):
                    for row in result[:5]:  # Show first 5 rows
                        print(row)
                else:
                    for row in result[:5]:
                        print(row)
                print("...")
        if output_csv:
            print(f"Note: Individual CSVs not saved when processing directory. Use single PDF for CSV output.")
    else:
        # Single PDF
        if not os.path.exists(pdf_path):
            print(f"PDF file {pdf_path} does not exist.")
            sys.exit(1)
        result = process_pdf(pdf_path, output_csv)
        if result:
            print("Extracted table:")
            if isinstance(result, list) and result and isinstance(result[0], dict):
                for row in result:
                    print(row)
            else:
                for row in result:
                    print(row)

if __name__ == "__main__":
    main()