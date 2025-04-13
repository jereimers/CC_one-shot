import sys
from pypdf import PdfReader
from pypdf.errors import PdfReadError

def list_form_fields(pdf_path):
    """Lists the form field names in a PDF."""
    try:
        reader = PdfReader(pdf_path)
        fields = reader.get_fields()
        if fields:
            print(f"Form fields found in '{pdf_path}':")
            # Sort fields alphabetically for easier reading
            for field_name in sorted(fields.keys()):
                field = fields[field_name]
                # Try to get common properties, handle potential missing keys
                field_type = field.get('/FT', 'N/A') # Field Type (e.g., /Tx for Text)
                field_value = field.get('/V', 'N/A') # Current Value
                mapping_name = field.get('/T', 'N/A') # Mapping Name (often the useful one)
                print(f"  - Mapping Name (T): {mapping_name}, Type (FT): {field_type}, Current Value (V): {field_value}")
        else:
            print(f"No form fields found in '{pdf_path}'.")
    except FileNotFoundError:
        print(f"Error: PDF file not found at '{pdf_path}'")
    except PdfReadError as e:
        print(f"Error reading PDF '{pdf_path}': {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python inspect_pdf_fields.py <path_to_pdf>")
        sys.exit(1)
    
    pdf_file_path = sys.argv[1]
    list_form_fields(pdf_file_path)
