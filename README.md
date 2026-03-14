# Congressional Budget Office PDF Scraper

This program is designed to scrape information from Congressional Budget Office Cost Estimate report PDFs. The data of interest is typically in the top summary table and include date range for the spending, possible estimated amounts, and "mandate effects".

The output is a csv file that contains information for further analysis.

Started 2026-03-13 1940 CST

## Installation

1. Install Python dependencies:
   ```
   pip install -r requirements.txt
   ```

## Usage

Run the script with a PDF file path or a directory containing PDFs:

```
python pdf_table_extractor.py path/to/your/file.pdf
python pdf_table_extractor.py path/to/directory/
```

The script now always writes a CSV at run completion. If you do not provide an output path, it auto-generates one:

- Single PDF: `<pdf_stem>_bill_input_rows.csv`
- Directory input: `<directory_name>_bill_input_rows.csv`

To override the CSV filename/path:

```
python pdf_table_extractor.py path/to/your/file.pdf output.csv
```

The script extracts the first table it finds in the PDF(s) and outputs it as a multidimensional array (list of lists) or dictionary (if headers are detected). It tries different extraction settings to handle various table formats.

## Features

- Extracts the first table from a PDF.
- Handles tables with or without headers.
- Outputs to console or CSV file.
- Designed for CBO cost estimate PDFs with summary tables.