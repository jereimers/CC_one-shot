import os
import logging
from dotenv import load_dotenv

# LlamaIndex imports
try:
    import faiss
    from llama_index.core import Settings, VectorStoreIndex, StorageContext, load_index_from_storage
    # Correct import path for the FAISS vector store integration
    from llama_index.vector_stores.faiss import FaissVectorStore
    from llama_index.embeddings.openai import OpenAIEmbedding
except ImportError as e:
    logging.error(f"LlamaIndex or FAISS import error: {e}. Make sure all dependencies are installed (including llama-index-vector-stores-faiss).")
    exit(1)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load environment variables
load_dotenv()

# --- Configuration ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
script_dir = os.path.dirname(os.path.abspath(__file__))
STORAGE_DIR = os.path.join(script_dir, "storage")
FAISS_INDEX_PATH = os.path.join(STORAGE_DIR, "faiss_index")

# Configure LlamaIndex settings (should match build_index.py)
# Ensure API key is set for embedding model if needed during load/query
if not OPENAI_API_KEY or OPENAI_API_KEY == "YOUR_OPENAI_API_KEY_HERE":
    logging.warning("OpenAI API Key not found or is placeholder. Embeddings might fail if needed.")
# Settings.embed_model = OpenAIEmbedding() # Setting it globally might not be needed if index loads embeddings

# Global variable to hold the loaded index/query engine
query_engine = None

def load_index_and_create_query_engine(index_path):
    """Loads the FAISS index from disk and creates a query engine."""
    global query_engine
    if query_engine:
        logging.info("Query engine already loaded.")
        return query_engine

    if not os.path.exists(index_path):
        logging.error(f"Index directory not found at: {index_path}")
        logging.error("Please run build_index.py first to create the index.")
        raise FileNotFoundError(f"Index not found at {index_path}")

    logging.info(f"Loading index from: {index_path}")
    try:
        # Load the vector store
        vector_store = FaissVectorStore.from_persist_dir(index_path)
        storage_context = StorageContext.from_defaults(
            vector_store=vector_store, persist_dir=index_path
        )
        # Load the index itself using the dedicated function
        logging.info("Loading index from storage context...")
        index = load_index_from_storage(storage_context=storage_context)
        logging.info("Index loaded successfully.")

        # Create a query engine (retriever)
        # You can customize similarity_top_k to retrieve more/fewer chunks
        query_engine = index.as_query_engine(similarity_top_k=3)
        logging.info("Query engine created.")
        return query_engine

    except Exception as e:
        logging.error(f"Error loading index or creating query engine: {e}", exc_info=True)
        raise

def query_index(query_text):
    """Queries the loaded index and returns retrieved context."""
    global query_engine
    if not query_engine:
        try:
            query_engine = load_index_and_create_query_engine(FAISS_INDEX_PATH)
        except Exception:
            return "Error: Could not load the index. Please ensure it has been built correctly."

    logging.info(f"Querying index with: '{query_text}'")
    try:
        response = query_engine.query(query_text)
        logging.info(f"Retrieved {len(response.source_nodes)} source nodes.")

        # Combine the text from the retrieved nodes
        context = "\n---\n".join([node.get_content() for node in response.source_nodes])
        return context

    except Exception as e:
        logging.error(f"Error during query execution: {e}", exc_info=True)
        return f"Error during query: {e}"

# Example usage (if run directly)
if __name__ == '__main__':
    print("Testing RAG retriever...")

    # Ensure API key is available if needed by the embedding model during query
    if not OPENAI_API_KEY or OPENAI_API_KEY == "YOUR_OPENAI_API_KEY_HERE":
        print("Warning: OpenAI API Key not set. Queries might fail if embeddings need recalculation.")

    try:
        # Load the engine (or confirm it's loaded)
        load_index_and_create_query_engine(FAISS_INDEX_PATH)
        print("Index loaded and query engine ready.")

        # Example query
        test_query = "What are the rules for concentration spells?"
        print(f"\nRunning test query: '{test_query}'")
        retrieved_context = query_index(test_query)

        print("\n--- Retrieved Context ---")
        print(retrieved_context[:1000] + "..." if len(retrieved_context) > 1000 else retrieved_context) # Print first 1000 chars
        print("--- End Retrieved Context ---")

    except FileNotFoundError:
        print(f"ERROR: Index not found at {FAISS_INDEX_PATH}. Run build_index.py first.")
    except Exception as e:
        print(f"An error occurred during testing: {e}")

    print("\nRAG retriever testing complete.")
