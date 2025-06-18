import os
import logging
import sys
from dotenv import load_dotenv

# Ensure the pdf_parser module can be found
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(script_dir)

try:
    from pdf_parser import extract_text_from_pdf
except ImportError:
    logging.error("Could not import extract_text_from_pdf from pdf_parser.py. Make sure it's in the same directory.")
    sys.exit(1)

# LlamaIndex imports (adjust based on specific version if needed)
try:
    import faiss
    from llama_index.core import Document, Settings, VectorStoreIndex, StorageContext
    from llama_index.core.node_parser import SentenceSplitter
    from llama_index.embeddings.openai import OpenAIEmbedding
    # Correct import path for the FAISS vector store integration
    from llama_index.vector_stores.faiss import FaissVectorStore
except ImportError as e:
    logging.error(f"LlamaIndex or FAISS import error: {e}. Make sure all dependencies are installed in the environment (including llama-index-vector-stores-faiss).")
    sys.exit(1)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load environment variables
load_dotenv()

# --- Configuration ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
BOOKS_DIR_REL = "../Books" # Relative path to the Books directory from this script's location
STORAGE_DIR = os.path.join(script_dir, "storage")
FAISS_INDEX_PATH = os.path.join(STORAGE_DIR, "faiss_index")

# LlamaIndex Settings (can be customized)
Settings.chunk_size = 512  # Size of text chunks
Settings.chunk_overlap = 50   # Overlap between chunks
# Use OpenAI for embeddings (requires API key)
Settings.embed_model = OpenAIEmbedding()
# Use SentenceSplitter for parsing text into nodes/chunks
Settings.node_parser = SentenceSplitter(chunk_size=Settings.chunk_size, chunk_overlap=Settings.chunk_overlap)


def load_documents_from_books(books_dir):
    """Loads text from PDFs in the specified directory into LlamaIndex Documents."""
    documents = []
    if not os.path.isdir(books_dir):
        logging.error(f"Books directory not found: {books_dir}")
        return []

    logging.info(f"Scanning for PDF files in: {books_dir}")
    for filename in os.listdir(books_dir):
        if filename.lower().endswith(".pdf"):
            pdf_path = os.path.join(books_dir, filename)
            logging.info(f"Processing PDF: {filename}")
            extracted_pages = extract_text_from_pdf(pdf_path)

            if extracted_pages is None:
                logging.error(f"Skipping {filename} due to extraction error.")
                continue
            if not extracted_pages:
                logging.warning(f"No text extracted from {filename}. Skipping.")
                continue

            # Create a Document object for each page containing text
            for page_num, text in extracted_pages:
                doc = Document(
                    text=text,
                    metadata={
                        "file_name": filename,
                        "page_label": str(page_num) # LlamaIndex expects string page labels
                    }
                )
                documents.append(doc)
            logging.info(f"Finished processing {filename}, added {len(extracted_pages)} pages.")
        else:
            logging.debug(f"Skipping non-PDF file: {filename}")

    logging.info(f"Total documents loaded from PDFs: {len(documents)}")
    return documents

def build_and_persist_index(documents, index_path):
    """Builds the FAISS index from documents and persists it."""
    if not documents:
        logging.error("No documents provided to build the index.")
        return False

    # Ensure storage directory exists
    storage_dir = os.path.dirname(index_path)
    os.makedirs(storage_dir, exist_ok=True)
    logging.info(f"Storage directory ensured: {storage_dir}")

    # Initialize FAISS vector store
    # Requires the dimensionality of the embeddings (OpenAI default is 1536)
    # faiss_index = faiss.IndexFlatL2(Settings.embed_model.embed_dim) # Correct way to get dim
    # Using default OpenAI model 'text-embedding-ada-002' which has 1536 dimensions
    d = 1536 # Dimension of OpenAI embeddings
    faiss_index = faiss.IndexFlatL2(d)
    vector_store = FaissVectorStore(faiss_index=faiss_index)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)

    logging.info("Building vector store index... (This may take a while and use API credits)")
    try:
        index = VectorStoreIndex.from_documents(
            documents,
            storage_context=storage_context,
            show_progress=True
        )
        logging.info("Index construction complete.")

        # Persist the index
        logging.info(f"Persisting index to: {index_path}")
        index.storage_context.persist(persist_dir=index_path)
        # vector_store.persist(persist_path=os.path.join(index_path, "vector_store.faiss")) # Old way? Check docs. Persist handles this.
        logging.info("Index persisted successfully.")
        return True

    except Exception as e:
        logging.error(f"Error building or persisting index: {e}", exc_info=True)
        # If it's an API key error, provide a specific message
        if "OPENAI_API_KEY" in str(e):
             logging.error("Please ensure your OPENAI_API_KEY is set correctly in the .env file.")
        return False


if __name__ == "__main__":
    print("--- Starting D&D Rulebook Index Builder ---")

    if not OPENAI_API_KEY or OPENAI_API_KEY == "YOUR_OPENAI_API_KEY_HERE":
        logging.error("OPENAI_API_KEY is not set or is still the placeholder in the .env file.")
        print("ERROR: Please set your OpenAI API key in the dnd_chatbot/.env file.")
        sys.exit(1)
    else:
        # Mask the key in logs just in case
        logging.info("OpenAI API Key loaded.")


    # Determine absolute path to Books directory
    books_dir_abs = os.path.abspath(os.path.join(script_dir, BOOKS_DIR_REL))
    print(f"Looking for PDF rulebooks in: {books_dir_abs}")

    # Load documents
    docs = load_documents_from_books(books_dir_abs)

    if docs:
        print(f"Loaded {len(docs)} document pages from PDFs.")
        # Build and persist index
        success = build_and_persist_index(docs, FAISS_INDEX_PATH)
        if success:
            print(f"--- Index successfully built and saved to: {FAISS_INDEX_PATH} ---")
        else:
            print("--- Index building failed. Check logs for details. ---")
            sys.exit(1)
    else:
        print("--- No documents were loaded. Index not built. ---")
        sys.exit(1)

    print("--- Index Builder Finished ---")
