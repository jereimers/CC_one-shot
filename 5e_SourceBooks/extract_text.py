import pdfplumber

def extract_text(pdf_path):
    text_pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            # fiddle with this extract_text function to tune the text extraction
            text = page.extract_text()
            if text:
                text_pages.append((page_num, text))
    return text_pages

# example usage
pdf_path = "D&D 5E - Dungeon Master's Guide.pdf"
pages = extract_text(pdf_path)
print(f"Extracted {len(pages)} pages.")