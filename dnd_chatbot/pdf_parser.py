import pdfplumber
import logging
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def extract_text_from_pdf(pdf_path):
    """
    Extracts text from each page of a PDF file.

    Args:
        pdf_path (str): The path to the PDF file.

    Returns:
        list: A list of tuples, where each tuple contains (page_number, page_text).
              Returns an empty list if the file doesn't exist or is not a PDF.
              Returns None if an error occurs during processing.
    """
    if not os.path.exists(pdf_path):
        logging.error(f"PDF file not found: {pdf_path}")
        return []
    if not pdf_path.lower().endswith(".pdf"):
        logging.warning(f"File is not a PDF: {pdf_path}")
        return []

    text_pages = []
    logging.info(f"Starting text extraction from: {pdf_path}")
    try:
        with pdfplumber.open(pdf_path) as pdf:
            num_pages = len(pdf.pages)
            logging.info(f"Opened PDF: {os.path.basename(pdf_path)} ({num_pages} pages)")
            for page_num, page in enumerate(pdf.pages, start=1):
                # Extract text using pdfplumber's default settings
                # You might need to adjust extraction parameters based on PDF quality/layout
                text = page.extract_text(x_tolerance=1, y_tolerance=3) # Small tolerances might help with layout
                if text:
                    # Basic cleaning: replace multiple newlines/spaces, strip whitespace
                    cleaned_text = ' '.join(text.split())
                    text_pages.append((page_num, cleaned_text))
                else:
                    logging.warning(f"No text extracted from page {page_num} of {os.path.basename(pdf_path)}")
            logging.info(f"Successfully extracted text from {len(text_pages)} pages in {os.path.basename(pdf_path)}")
        return text_pages
    except Exception as e:
        logging.error(f"Error processing PDF {pdf_path}: {e}", exc_info=True)
        return None # Indicate an error occurred

# Example usage (if run directly)
if __name__ == '__main__':
    print("Testing pdf_parser...")
    # Assuming the script is in dnd_chatbot and Books is a sibling directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(script_dir)
    example_pdf_dir = os.path.join(parent_dir, "Books")
    example_pdf_path = os.path.join(example_pdf_dir, "D&D 5E - Player's Handbook.pdf") # Choose a sample PDF

    if os.path.exists(example_pdf_path):
        pages = extract_text_from_pdf(example_pdf_path)
        if pages is None:
            print("An error occurred during extraction.")
        elif pages:
            print(f"Extracted {len(pages)} pages with text.")
            # Print text from the first page with content (up to 500 chars)
            print("\n--- Example Text (Page", pages[0][0], ") ---")
            print(pages[0][1][:500] + "...")
            print("--- End Example ---")
        else:
            print("No text could be extracted from the example PDF.")
    else:
        print(f"Example PDF not found at expected location: {example_pdf_path}")

    print("\nPDF parser testing complete.")
