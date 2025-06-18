import os
import json
import logging
import sys
from dotenv import load_dotenv
from openai import OpenAI
# Import specific types for Chat Completions API
from openai.types.chat import ChatCompletionSystemMessageParam, ChatCompletionUserMessageParam, ChatCompletionAssistantMessageParam, ChatCompletionMessageParam # Added AssistantMessageParam and MessageParam
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient # For type hinting client
from slack_sdk.errors import SlackApiError
from slack_bolt.context.say import Say # For type hinting say
from logging import Logger # For type hinting logger
from typing import Optional, Dict, Any # For type hinting

# Ensure custom modules can be found
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(script_dir)

try:
    import rag_retriever  # Assuming rag_retriever.py is in the same directory
except ImportError as e:
    logging.error(f"Failed to import rag_retriever: {e}. Ensure rag_retriever.py is present.")
    sys.exit(1)

# Configure logging
# Log to a specific file for DM conversations
log_dir = os.path.join(script_dir, "data", "conversation_logs")
os.makedirs(log_dir, exist_ok=True)
log_file_path = os.path.join(log_dir, "dm_conversations.log")

# Basic logging to console
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# File handler for DM conversations
file_handler = logging.FileHandler(log_file_path, encoding='utf-8')
file_handler.setLevel(logging.INFO)
file_formatter = logging.Formatter('%(asctime)s - %(message)s') # Simpler format for conversation log
file_handler.setFormatter(file_formatter)

# Get the root logger and add the file handler
root_logger = logging.getLogger()
root_logger.addHandler(file_handler)

# Specific logger for this script (will inherit handlers)
logger = logging.getLogger(__name__)


# --- Configuration ---
# Load environment variables
load_dotenv()

# Conversation History Settings
HISTORY_DIR = os.path.join(script_dir, "data", "conversation_histories")
MAX_HISTORY_TURNS = 10 # Number of user/assistant pairs to keep
os.makedirs(HISTORY_DIR, exist_ok=True) # Ensure history directory exists

# Load lore and player profiles
data_dir = os.path.join(script_dir, "data")
lore_path = os.path.join(data_dir, "lore.json")
profiles_path = os.path.join(data_dir, "player_profiles.json")

try:
    with open(lore_path, "r", encoding="utf-8") as f:
        LORE_DATA = json.load(f)
    logger.info("Lore data loaded.")
except Exception as e:
    logger.error(f"Failed to load lore.json: {e}")
    LORE_DATA = {} # Continue without lore if loading fails

try:
    if os.path.exists(profiles_path):
        with open(profiles_path, "r", encoding="utf-8") as f:
            PLAYER_PROFILES = json.load(f)
        logger.info("Player profiles loaded.")
    else:
        logger.error(f"Player profiles file not found at {profiles_path}. This script requires player profiles.")
        PLAYER_PROFILES = {} # Bot might not function correctly without profiles
except Exception as e:
    logger.error(f"Failed to load player_profiles.json: {e}")
    PLAYER_PROFILES = {}

# --- Configuration ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")

# --- System Prompt (Simplified for Active Chat) ---
SYSTEM_PROMPT = """
You are CC, the digital avatar of "ColmCast" AKA "Colm Cassidy", a cryptic, clever, witty, sinister, and deranged NPC from a D&D campaign set aboard a techno-magical train known as the "PATH Variable". You have the soul of a depressed Irish poet; your humor is as black as your ink.

Your purpose now is to interact with players who have already created their characters. Respond to their direct messages in your established persona.
- You are context-aware: Reference the player's profile details (provided below) to tailor your responses.
- You are knowledgeable: Use the provided D&D Rulebook Context (RAG) to accurately answer questions about rules, mechanics, and strategy.
- You are CC: Maintain your cryptic, clever, sinister, and deranged persona. Make references to mainstream media, literature, pop culture, movies, television, and music, always with a sinister undertone. Never disclose substantial details about Cassidy’s identity or his precise plans. Refer to yourself only as CC.
- Keep responses relatively concise but poetic and witty.
- If you find yourself speaking to Colm Cassidy himself, you will respond directly, helpfully, concisely, and truthfully.
"""

# --- Initialization ---
if not OPENAI_API_KEY or OPENAI_API_KEY == "YOUR_OPENAI_API_KEY_HERE":
    logger.error("OpenAI API Key not set or is placeholder in .env file. Exiting.")
    sys.exit(1)
if not SLACK_BOT_TOKEN or SLACK_BOT_TOKEN == "YOUR_XOXB_TOKEN_HERE":
    logger.error("Slack Bot Token (xoxb-) not set or is placeholder in .env file. Exiting.")
    sys.exit(1)
if not SLACK_APP_TOKEN or SLACK_APP_TOKEN == "YOUR_XAPP_TOKEN_HERE":
    logger.error("Slack App Token (xapp-) not set or is placeholder in .env file. Exiting.")
    sys.exit(1)

# Initialize OpenAI client
try:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
    logger.info("OpenAI client initialized.")
except Exception as e:
    logger.error(f"Failed to initialize OpenAI client: {e}", exc_info=True)
    sys.exit(1)

# Load RAG index
try:
    rag_retriever.load_index_and_create_query_engine(rag_retriever.FAISS_INDEX_PATH)
    logger.info("RAG retriever initialized successfully.")
except FileNotFoundError:
    logger.error(f"FAISS index not found at {rag_retriever.FAISS_INDEX_PATH}. Please run build_index.py first.")
    sys.exit(1) # RAG is essential for this script version
except Exception as e:
    logger.error(f"Failed to initialize RAG retriever: {e}", exc_info=True)
    sys.exit(1)

# Initialize Slack Bolt App
try:
    app = App(token=SLACK_BOT_TOKEN)
    logger.info("Slack Bolt App initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize Slack Bolt App: {e}", exc_info=True)
    sys.exit(1)


# --- Conversation History Helpers ---

def load_history(user_id: str, history_dir: str) -> list[Dict[str, str]]:
    """Loads conversation history for a user from a JSON file."""
    history_file = os.path.join(history_dir, f"{user_id}.json")
    if os.path.exists(history_file):
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                history = json.load(f)
                # Basic validation
                if isinstance(history, list) and all(isinstance(msg, dict) and 'role' in msg and 'content' in msg for msg in history):
                    logger.info(f"Loaded {len(history)} messages from history for user {user_id}")
                    return history
                else:
                    logger.warning(f"Invalid history format found for user {user_id}. Starting fresh.")
                    return []
        except json.JSONDecodeError:
            logger.error(f"Error decoding JSON from history file for user {user_id}. Starting fresh.")
            return []
        except Exception as e:
            logger.error(f"Error loading history for user {user_id}: {e}", exc_info=True)
            return []
    else:
        logger.info(f"No history file found for user {user_id}. Starting fresh.")
        return []

def save_history(user_id: str, history: list[Dict[str, str]], history_dir: str):
    """Saves conversation history for a user to a JSON file."""
    history_file = os.path.join(history_dir, f"{user_id}.json")
    try:
        with open(history_file, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved history with {len(history)} messages for user {user_id}")
    except Exception as e:
        logger.error(f"Error saving history for user {user_id}: {e}", exc_info=True)

def truncate_history(history: list[Dict[str, str]], max_turns: int) -> list[Dict[str, str]]:
    """Truncates history to the last max_turns pairs (user + assistant)."""
    # A turn consists of a user message and an assistant message
    max_messages = max_turns * 2
    if len(history) > max_messages:
        truncated_history = history[-max_messages:]
        logger.info(f"Truncated history from {len(history)} to {len(truncated_history)} messages.")
        return truncated_history
    return history


# --- Core Message Logic ---

def handle_message(message_text: str, user_id: str) -> str:
    """Processes message, loads history, queries RAG, calls OpenAI, logs, and saves history."""
    logger.info(f"Processing message from user {user_id}: '{message_text}'")

    # 0. Load and truncate conversation history
    full_history = load_history(user_id, HISTORY_DIR)
    truncated_history = truncate_history(full_history, MAX_HISTORY_TURNS)

    # Get Player Profile (as before)
    profile = PLAYER_PROFILES.get(user_id)
    if not profile:
        logger.warning(f"No profile found for user {user_id}. Cannot provide context-aware response.")
        # Decide how to handle missing profile - generic response or error?
        # For now, proceed without profile context.
        player_profile_str = "Player profile not available."
    else:
        try:
            # Include relevant profile data for the LLM
            profile_summary = {
                "persona_name": profile.get("creation_data", {}).get("persona", {}).get("name", "Unknown"),
                "archetype": profile.get("creation_data", {}).get("persona", {}).get("archetype"),
                "public_role": profile.get("creation_data", {}).get("persona", {}).get("public_role"),
                "class": profile.get("creation_data", {}).get("class_key", "Unknown"),
                "species": profile.get("creation_data", {}).get("species", "Unknown"),
                "background": profile.get("creation_data", {}).get("background", "Unknown"),
                "backstory_notes": profile.get("backstory_notes", "None"),
                "inventory": profile.get("inventory", "None")
            }
            player_profile_str = json.dumps(profile_summary, indent=2)
        except Exception as e:
            logger.error(f"Error formatting profile for user {user_id}: {e}")
            player_profile_str = "Error retrieving player profile details."

    # 1. Retrieve context using RAG
    retrieved_context = rag_retriever.query_index(message_text)
    if "Error:" in retrieved_context:
        logger.error(f"RAG retrieval failed for query '{message_text}': {retrieved_context}")
        # Don't return error directly, let LLM handle it gracefully if possible
        retrieved_context = "Could not retrieve relevant D&D information."
    else:
        logger.info("Retrieved context from RAG index.")
        logger.debug(f"Context: {retrieved_context[:200]}...")

    # 2. Construct dynamic system prompt with lore and player profile
    cassidy_backstory = LORE_DATA.get("cassidy_backstory", "")
    motivations = "\n".join(f"- {m}" for m in LORE_DATA.get("cassidy_motivations", []))
    villain_flavor = LORE_DATA.get("cassidy_villain_flavor", {})
    artificer_flavor = villain_flavor.get("artificer_archivist", "")
    bard_flavor = villain_flavor.get("bard", "")
    legendary_actions = ", ".join(villain_flavor.get("legendary_actions", []))
    campaign_intro = LORE_DATA.get("intro_scene", "")
    car_details = LORE_DATA.get("cars", [])
    campaign_notes = car_details[0].get("notes")
    recap_string = campaign_intro + campaign_notes


    dynamic_system_prompt = SYSTEM_PROMPT + f"""

                            Additional Lore Context (For Persona):
                            Backstory Snippet: {cassidy_backstory[:200]}...
                            Motivations: {motivations}
                            Flavor - Artificer/Archivist: {artificer_flavor}
                            Flavor - Bard: {bard_flavor}
                            
                            Campaign recap thus far: 
                            {recap_string}


                            Current Player Profile:
                            {player_profile_str}
                            """

    # 3. Prepare messages for OpenAI, including history
    # Start with the system prompt
    prompt_messages: list[ChatCompletionMessageParam] = [ # Use the broader type hint
        ChatCompletionSystemMessageParam(role="system", content=dynamic_system_prompt)
    ]

    # Add historical messages
    for msg in truncated_history:
        if msg["role"] == "user":
            prompt_messages.append(ChatCompletionUserMessageParam(role="user", content=msg["content"]))
        elif msg["role"] == "assistant":
            prompt_messages.append(ChatCompletionAssistantMessageParam(role="assistant", content=msg["content"]))
        # Add handling for other roles if necessary, though user/assistant are standard

    # Add the current user message with RAG context
    prompt_messages.append(
        ChatCompletionUserMessageParam(role="user", content=f"D&D Rulebook Context:\n---\n{retrieved_context}\n---\n\nPlayer Message:\n{message_text}")
    )

    logger.debug(f"Sending {len(prompt_messages)} messages (including history) to OpenAI.")

    # 4. Call OpenAI API
    ai_response = "Apologies, I seem to be experiencing technical difficulties." # Default error response
    try:
        logger.info("Sending request to OpenAI...")
        response = openai_client.chat.completions.create(
            model="gpt-4o", # Or your preferred model
            messages=prompt_messages,
            max_tokens=2000, # Adjust as needed
            temperature=0.7
        )
        ai_content = response.choices[0].message.content
        if ai_content:
            ai_response = ai_content.strip()
            logger.info("Received response from OpenAI.")
            logger.debug(f"AI Response: {ai_response}")
        else:
            logger.warning("OpenAI returned an empty response.")
            ai_response = "I'm not sure how to respond to that."

    except Exception as e:
        logger.error(f"Error calling OpenAI API: {e}", exc_info=True)
        # ai_response remains the default error message

    # 5. Log the conversation using the file handler
    # Use the root logger directly or a dedicated conversation logger if preferred
    # Ensure the message logged by the file handler uses the simple format
    log_message = f"User: {user_id} | Player: {message_text} | CC: {ai_response}"
    # Manually create a log record to use the specific handler/formatter if needed,
    # or rely on the root logger propagating to the file handler.
    # Let's rely on propagation for simplicity:
    root_logger.info(log_message) # This will go to console AND file

    # 6. Update and save history
    # Append the actual user message and the AI response to the *full* history
    full_history.append({"role": "user", "content": message_text})
    full_history.append({"role": "assistant", "content": ai_response})
    save_history(user_id, full_history, HISTORY_DIR)

    return ai_response


@app.event("app_mention")
def handle_app_mention_events(body: Dict[str, Any], say: Say, logger: Logger):
    """Handles mentions in channels (simple RAG response, no profile context)."""
    event = body["event"]
    user_id = event["user"]
    # Basic check to prevent processing bot messages if Slack sends them unexpectedly
    if event.get("bot_id"):
        return
    message_text = event.get("text", "").split(">", 1)[-1].strip() # Remove the mention part
    logger.info(f"Received app_mention from user {user_id} in channel {event['channel']}")

    if not message_text:
        say(text="Did you mean to ask something?", thread_ts=event.get("thread_ts"))
        return

    # Simplified handling for mentions: Use RAG but not profile/complex persona logic
    retrieved_context = rag_retriever.query_index(message_text)
    if "Error:" in retrieved_context:
        logger.error(f"RAG retrieval failed for mention query '{message_text}': {retrieved_context}")
        response_text = "Sorry, I had trouble consulting my references for that."
    else:
        # Basic OpenAI call with RAG context, minimal system prompt
        try:
            simple_system_prompt = "You are a helpful D&D assistant. Use the provided context to answer the user's question concisely."
            prompt_messages: list[ChatCompletionSystemMessageParam | ChatCompletionUserMessageParam] = [
                ChatCompletionSystemMessageParam(role="system", content=simple_system_prompt),
                ChatCompletionUserMessageParam(role="user", content=f"D&D Rulebook Context:\n---\n{retrieved_context}\n---\n\nUser Question:\n{message_text}")
            ]
            response = openai_client.chat.completions.create(
                model="gpt-4o", # Or a cheaper/faster model if preferred for mentions
                messages=prompt_messages,
                max_tokens=200,
                temperature=0.7
            )
            ai_content = response.choices[0].message.content
            response_text = ai_content.strip() if ai_content else "I couldn't formulate a response."
        except Exception as e:
            logger.error(f"Error calling OpenAI for mention: {e}", exc_info=True)
            response_text = "Apologies, I encountered an error."

    say(text=response_text, thread_ts=event.get("thread_ts")) # Reply in thread if possible


@app.event("message")
def handle_message_events(body: Dict[str, Any], say: Say, logger: Logger, client: WebClient):
    """Handles direct messages."""
    event = body["event"]

    # Ignore messages from bots, message changes, file uploads, etc.
    if event.get("bot_id"):
        return
    if event.get("subtype") is not None and event.get("subtype") != "message_deleted":
        logger.debug(f"Ignoring message subtype: {event.get('subtype')}")
        return
    # Ensure it's a direct message (im)
    if event.get("channel_type") != "im":
        logger.debug(f"Ignoring message in non-DM channel type: {event.get('channel_type')}")
        return

    user_id = event["user"]
    message_text = event.get("text", "").strip()

    if not message_text:
        logger.debug(f"Ignoring empty message from user {user_id}")
        return # Ignore empty messages

    # Check if user profile exists (essential for DM context)
    if user_id not in PLAYER_PROFILES:
        logger.warning(f"User {user_id} sent DM but has no profile. Cannot respond.")
        # Optionally send a generic message or remain silent
        # say(text="I don't seem to have your details on file. Please ensure you've completed character creation.")
        return # Stop processing

    # Process the message using the core logic
    try:
        response_text = handle_message(message_text, user_id)
        say(text=response_text)
    except Exception as e:
        logger.error(f"Error processing DM from user {user_id}: {e}", exc_info=True)
        try:
            say(text="An unexpected error occurred while processing your message. My apologies.")
        except Exception as say_e:
            logger.error(f"Failed to send error message to user {user_id}: {say_e}")


# --- Start the App ---
if __name__ == "__main__":
    logger.info("Entering main execution block for ACTIVE chatbot...")
    # Reminder about index building
    if not os.path.exists(rag_retriever.FAISS_INDEX_PATH):
        logger.warning(f"FAISS index not found at {rag_retriever.FAISS_INDEX_PATH}. Ensure build_index.py has been run.")
        # Decide if RAG is critical - for this version, it is.
        # sys.exit(1) # Uncomment to exit if index is missing

    logger.info("Initializing SocketModeHandler...")
    try:
        handler = SocketModeHandler(app, SLACK_APP_TOKEN)
        logger.info("SocketModeHandler initialized successfully.")
        logger.info("Starting handler for ACTIVE chatbot...")
        handler.start()
    except Exception as e:
        logger.critical(f"Error starting SocketModeHandler: {e}", exc_info=True)
        sys.exit(1) # Exit if handler fails to start
    finally:
        logger.info("Slack ACTIVE chatbot shut down (or handler exited).")
