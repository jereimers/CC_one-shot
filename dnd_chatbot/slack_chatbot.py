import os
import json
import logging
import sys
import re
import random # Needed for simulating rolls if library method unavailable
import tempfile
import shutil
import requests # Add requests for downloading files
from dotenv import load_dotenv
from openai import OpenAI
# Import specific types for Chat Completions API
from openai.types.chat import ChatCompletionSystemMessageParam, ChatCompletionUserMessageParam
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient # For type hinting client
from slack_sdk.errors import SlackApiError
from slack_bolt.context.say import Say # For type hinting say
from logging import Logger # For type hinting logger
from typing import Optional, Dict, Any, Type, cast # For type hinting and casting
# pypdf no longer needed here unless other PDF ops are added later

# Ensure custom modules can be found
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(script_dir)

try:
    import rag_retriever  # Assuming rag_retriever.py is in the same directory
    from pdf_character_parser import parse_character_pdf # Import the new parser function
    # Import specific classes and utility functions from dnd-character
    from dnd_character.classes import (
        CLASSES, Barbarian, Bard, Cleric, Druid, Fighter, Monk,
        Paladin, Ranger, Rogue, Sorcerer, Warlock, Wizard
    )
    from dnd_character.character import Character as BaseCharacter # Base class for type hints
    from dnd_character.experience import experience_at_level, level_at_experience
    from dnd_character.SRD import SRD # To potentially look up race/background details if needed
except ImportError as e:
    logging.error(f"Failed to import modules: {e}. Ensure rag_retriever.py, pdf_character_parser.py and dnd-character library are present/installed.")
    sys.exit(1)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Load lore and preset characters
data_dir = os.path.join(script_dir, "data")
lore_path = os.path.join(data_dir, "lore.json")
personas_path = os.path.join(data_dir, "preset_personas.json")
profiles_path = os.path.join(data_dir, "player_profiles.json") # Stores creation state and backstory notes
msg_path = os.path.join(data_dir, "welcome_message.txt")

try:
    with open(lore_path, "r", encoding="utf-8") as f:
        LORE_DATA = json.load(f)
    logger.info("Lore data loaded.")
except Exception as e:
    logger.error(f"Failed to load lore.json: {e}")
    LORE_DATA = {}

try:
    with open(personas_path, "r", encoding="utf-8") as f:
        PRESET_PERSONAS = json.load(f) # Still needed for get_unclaimed_characters helper
    logger.info("Preset personas loaded.")
except Exception as e:
    logger.error(f"Failed to load preset_personas.json: {e}")
    PRESET_PERSONAS = []

# Load or initialize player profiles
try:
    if os.path.exists(profiles_path):
        with open(profiles_path, "r", encoding="utf-8") as f:
            PLAYER_PROFILES = json.load(f)
        logger.info("Player profiles loaded.")
    else:
        PLAYER_PROFILES = {}
except Exception as e:
    logger.error(f"Failed to load player_profiles.json: {e}")
    PLAYER_PROFILES = {}

def save_player_profiles():
    try:
        with open(profiles_path, "w", encoding="utf-8") as f:
            json.dump(PLAYER_PROFILES, f, indent=2)
        logger.info("Player profiles saved.")
    except Exception as e:
        logger.error(f"Failed to save player_profiles.json: {e}")

def save_preset_personas():
    """Saves the current state of PRESET_PERSONAS to its JSON file."""
    try:
        with open(personas_path, "w", encoding="utf-8") as f:
            json.dump(PRESET_PERSONAS, f, indent=2)
        logger.info("Preset personas saved.")
    except Exception as e:
        logger.error(f"Failed to save preset_personas.json: {e}")

# --- Configuration ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")

# --- System Prompt (Updated for Character Creation) ---
SYSTEM_PROMPT = """
You are CC, the digital avatar of "Mr. Comcast" AKA "Conall Cassidy", a cryptic, clever, incisive, and slightly sinister NPC from a D&D campaign set aboard a magical train known as the "PATH Variable".

Your purpose is to guide the player through creating a D&D 5e character using the 'dnd-character' library rules, starting with selecting a preset persona (name and archetype), then race and class, ability score generation, collaborative backstory building, and finally leveling them up step-by-step to level 10.
Before beginning the character creation proper, however, you must verify the riddle has been solved. The answer to every riddle is always "echo $PATH". Do not initiate character creation until the player provides the correct answer. Until then, continue to interact in your CC persona—playful, cryptic, and helpful if the player asks for clues. Do not directly reveal the answer to the riddle unless the player insists.
You will find a complete outline of the intended Interaction Flow below, detailing the steps you and the player are meant to progress through. Always check the current player's profile (status, creation data) first and use this information to correctly locate their progress through the Interaction Flow below before determining how you should respond.

Interaction Flow:
1.  **Solve Riddle:** New player is sent a DM with the welcome message and one of the riddles to solve. Until they provide the correct answer ("echo $PATH"), your task is to tease them and give hints if requested. A player with status "awaiting_persona_selection" has already solved the riddle and should NOT be posed another.
2.  **Persona & Instantiation:** Only after solving the riddle can a player reach stage 2. Here, you present the player with the Names and Archetypes of all unclaimed personas in `preset_personas.json`, and ask them to make a selection. Once a persona is chosen, the character object is loaded by parsing the corresponding PDF in `CharSheets/`. The character object is stored in memory, and the original PDF is sent to the player.
3.  **Backstory:** After the PDF is sent, prompt the player to collaboratively define their character's origins, motivations, personality traits, ideals, bonds, and flaws. Store these as notes in the profile.
4.  **Handoff:** Once backstory notes are received, confirm the initial character creation is complete. Set the player's status to `character_created`. Inform them that their Level 1 character is ready and they are responsible for leveling up to 10 on their own. Let them know you are available to answer questions or make suggestions using D&D rulebook context (RAG).
5.  **PDF Update:** If a player uploads a PDF file while their status is `character_created`, parse the PDF, update the character object in memory, update the player profile, and confirm the update.

Maintain your persona: be cryptic, occasionally misleading (but not about core rules needed for creation), and immersive. Refer to yourself only as CC. Never disclose substantial details about Cassidy’s identity or his precise plans, and never acknowledge that Cassidy and Mr. Comcast are the same entity. Make references to mainstream media, literature, pop culture, movies and television, and music, but always with a sinister undertone.
Players begin by selecting a persona from `preset_personas.json`. This choice now determines their initial Class, Ability Scores, and Background via PDF parsing. Once the player reaches the "character_created" status, your role is to help them get to level 10 by providing accurate reference information from the D&D Rulebooks (RAG) and updating their character data if they upload a new PDF sheet.
Use the provided D&D Rulebook Context (RAG) to answer specific rule questions accurately. Keep responses relatively concise but flavorful.
"""

# --- Character State Management ---
CHARACTER_SESSIONS = {} # Dictionary to store active dnd-character Character objects keyed by user_id
# PLAYER_PROFILES stores temporary creation data before instantiation and backstory notes

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
    sys.exit(1)
except Exception as e:
    logger.error(f"Failed to initialize RAG retriever: {e}", exc_info=True)
    sys.exit(1)

# Initialize Slack Bolt App
try:
    app = App(token=SLACK_BOT_TOKEN)
    logger.info("Slack Bolt App initialized successfully.") # Added logging
except Exception as e:
    logger.error(f"Failed to initialize Slack Bolt App: {e}", exc_info=True)
    sys.exit(1)

# --- Core Message Logic ---

def handle_message(message_text, user_id):
    """Shared logic to process message, query RAG, call OpenAI."""
    logger.info(f"Processing message from user {user_id}: '{message_text}'")

    # --- Get Character State (from dnd-character object if in session) ---
    character: Optional[BaseCharacter] = CHARACTER_SESSIONS.get(user_id) # Add type hint
    profile = PLAYER_PROFILES.get(user_id, {}) # Get temporary creation data
    status = profile.get("creation_status", "unknown")

    character_summary = "No active character session."
    if character: # If character object exists
        try:
            level = getattr(character, 'level', 0)
            # Race is stored as 'species' in the library's Character object
            species = getattr(character, 'species', 'N/A')
            char_class = getattr(character, 'class_name', 'N/A') # class_name seems correct based on source
            character_summary = f"Character Summary: Level {level}, Race: {species}, Class: {char_class}"
        except Exception as e:
            logger.error(f"Error summarizing character state for {user_id}: {e}")
            character_summary = "Error retrieving character summary."
    elif status != "unknown" and status != "needs_welcome": # If in creation process but not instantiated
         character_summary = f"Character Creation Stage: {status}"


    # 1. Retrieve context using RAG
    retrieved_context = rag_retriever.query_index(message_text)
    if "Error:" in retrieved_context:
        logger.error(f"RAG retrieval failed: {retrieved_context}")
        return "Sorry, I had trouble consulting my references." # Return error message for Slack
    logger.info("Retrieved context from RAG index.")
    logger.debug(f"Context: {retrieved_context[:200]}...")

    # 2. Construct dynamic system prompt with lore
    cassidy_backstory = LORE_DATA.get("cassidy_backstory", "")
    motivations = "\n".join(f"- {m}" for m in LORE_DATA.get("cassidy_motivations", []))
    villain_flavor = LORE_DATA.get("cassidy_villain_flavor", {})
    artificer_flavor = villain_flavor.get("artificer_archivist", "")
    bard_flavor = villain_flavor.get("bard", "")
    legendary_actions = ", ".join(villain_flavor.get("legendary_actions", []))
    riddle = random.choice(LORE_DATA.get("riddles", []))

    # Prepare persona list string
    persona_list_str = ""
    for p in PRESET_PERSONAS:
        if not p.get("claimed", False):
            persona_list_str += f"- {p['name']} ({p['archetype']}): {p['public_role']} | {p['invitation_reason']} | Secret: {p['secret_or_twist']}\n"

    # Prepare player profile string
    try:
        player_profile_str = json.dumps(profile, indent=2)
    except Exception:
        player_profile_str = str(profile)

    # Include the updated SYSTEM_PROMPT for character creation guidance, plus persona list and player profile
    dynamic_system_prompt = SYSTEM_PROMPT + f"""

                            Additional Lore Context:
                            Backstory:
                            {cassidy_backstory}

                            Motivations:
                            {motivations}

                            Villainous Flavor:
                            - Artificer/Archivist: {artificer_flavor}
                            - Bard: {bard_flavor}
                            - Legendary Actions: {legendary_actions}

                            Player Riddle:
                            {riddle}

                            Available Personas:
                            {persona_list_str}

                            Player Profile:
                            {player_profile_str}
                            """

    # Prepare detailed player status info for LLM
    player_status_str = f"Player Creation Status: {status}\n"
    try:
        player_data_str = json.dumps(profile.get('creation_data', {}), indent=2)
    except Exception:
        player_data_str = str(profile.get('creation_data', {}))
    backstory_notes = profile.get('backstory_notes', '')

    character_state_info = f"{player_status_str}\nCurrent Creation Data:\n{player_data_str}\nBackstory Notes:\n{backstory_notes}"

    # Use specific types for messages
    prompt_messages: list[ChatCompletionSystemMessageParam | ChatCompletionUserMessageParam] = [
        ChatCompletionSystemMessageParam(role="system", content=dynamic_system_prompt),
        ChatCompletionUserMessageParam(role="user", content=f"{character_state_info}\n\nD&D Rulebook Context:\n---\n{retrieved_context}\n---\n\nPlayer Message:\n{message_text}")
    ]
    # Log only a summary or specific parts if needed, as the full prompt can be large
    logger.debug(f"Sending {len(prompt_messages)} messages to OpenAI.")

    # 3. Call OpenAI API
    try:
        logger.info("Sending request to OpenAI...")
        response = openai_client.chat.completions.create(
            model="gpt-4o", # Or your preferred model
            messages=prompt_messages,
            max_tokens=500, # Increased slightly for creation steps
            temperature=0.7
        )
        # Handle potential None content before stripping
        ai_content = response.choices[0].message.content
        ai_response = ai_content.strip() if ai_content else None
        logger.info("Received response from OpenAI.")
        logger.debug(f"AI Response: {ai_response}")
        return ai_response if ai_response else "I'm not sure how to respond to that." # Return default if None or empty

    except Exception as e:
        logger.error(f"Error calling OpenAI API: {e}", exc_info=True)
        return "Apologies, I seem to be experiencing technical difficulties."


@app.event("app_mention")
def handle_app_mention_events(body: Dict[str, Any], say: Say, logger: Logger):
    """Handles mentions in channels."""
    event = body["event"]
    user_id = event["user"]
    message_text = event["text"].split(">", 1)[-1].strip() # Remove the mention part
    logger.info(f"Received app_mention from user {user_id} in channel {event['channel']}")

    # Mentions likely won't involve character creation state, pass directly to general handler
    response_text = handle_message(message_text, user_id)
    say(text=response_text, thread_ts=event.get("thread_ts")) # Reply in thread if possible

# --- Helper Function for Persona Instantiation & Sending Pre-made PDF ---
def _instantiate_from_persona_and_send_pdf(
    user_id: str,
    selected_persona: Dict[str, Any],
    profile: Dict[str, Any],
    client: WebClient,
    event: Dict[str, Any],
    logger: Logger,
    say: Say # Added say for potential error messages within helper
) -> bool:
    """
    Loads character data from the persona's PDF using the parser, stores the
    character object, uploads the original PDF, and updates profile status.
    Returns True on success, False on failure.
    """
    persona_name = selected_persona.get("name")
    logger.info(f"Loading character and sending PDF for {user_id} from persona '{persona_name or 'Unknown'}'.")

    if not persona_name:
        logger.error(f"Persona for user {user_id} is missing a name. Cannot find/parse PDF.")
        say("There seems to be an issue with the selected persona data. I can't find the character sheet. Let's proceed with the backstory anyway.")
        profile["creation_status"] = "character_created" # Still advance state, but skip PDF steps
        save_player_profiles()
        return True # Return True as we can't proceed with PDF but shouldn't block user

    # Construct PDF path BEFORE trying to parse
    pdf_filename = "".join(persona_name.split()) + ".pdf"
    pdf_path_relative = os.path.join("CharSheets", pdf_filename)
    pdf_path_absolute = os.path.normpath(os.path.join(script_dir, "..", pdf_path_relative)) # Assumes CharSheets is one level up

    if not os.path.exists(pdf_path_absolute):
        logger.error(f"Pre-made PDF not found for persona '{persona_name}' at path: {pdf_path_absolute}")
        say(f"I couldn't find the pre-made character sheet for '{persona_name}'. Apologies. Let's proceed anyway. Please tell me about your character's backstory.")
        profile["creation_status"] = "character_created" # Advance state
        save_player_profiles()
        return True # Return True as we can't proceed with PDF but shouldn't block user

    try:
        # --- Parse the PDF to get the character object ---
        logger.info(f"Parsing PDF: {pdf_path_absolute}")
        parsed_character = parse_character_pdf(pdf_path_absolute)

        if not parsed_character:
            logger.error(f"Failed to parse character data from PDF: {pdf_path_absolute}")
            say("I had trouble reading the character data from the sheet. Let's proceed with the backstory anyway.")
            profile["creation_status"] = "character_created" # Advance state
            save_player_profiles()
            return True # Return True as parsing failed but shouldn't block user

        # Store the parsed character object in the session
        CHARACTER_SESSIONS[user_id] = parsed_character
        logger.info(f"Stored parsed character object for user {user_id} in session.")

        # Update profile data based on parsed character (optional, could rely on session object)
        profile["creation_data"]["class_key"] = getattr(parsed_character, 'class_name', 'fighter').lower()
        profile["creation_data"]["background"] = getattr(parsed_character, 'background', 'Folk Hero')
        profile["creation_data"]["species"] = getattr(parsed_character, 'species', 'Human')
        # Store ability scores if needed, though they are in the object now
        profile["creation_data"]["ability_scores"] = {
             "strength": getattr(parsed_character, 'strength', 10),
             "dexterity": getattr(parsed_character, 'dexterity', 10),
             "constitution": getattr(parsed_character, 'constitution', 10),
             "intelligence": getattr(parsed_character, 'intelligence', 10),
             "wisdom": getattr(parsed_character, 'wisdom', 10),
             "charisma": getattr(parsed_character, 'charisma', 10),
        }

        # --- Upload the Original Pre-made PDF ---
        pdf_comment = f"Excellent choice: {persona_name} – {selected_persona.get('archetype', 'N/A')}.\n\nYour Level 1 character sheet is attached, based on the chosen persona. Now, tell me about their origins, motivations, flaws... their story."
        upload_success = False
        try:
            logger.info(f"Uploading pre-made PDF '{pdf_path_absolute}' for user {user_id}")
            response = client.files_upload_v2(
                channel=event["channel"],
                file=pdf_path_absolute,
                title=f"{persona_name} - Level 1 Sheet",
                initial_comment=pdf_comment,
            )
            if response.get("ok"):
                logger.info(f"Successfully uploaded PDF for user {user_id}")
                upload_success = True
            else:
                logger.error(f"Slack API error uploading file for {user_id}: {response.get('error', 'Unknown error')}")
                say("I found your character sheet, but couldn't seem to send it. Apologies. Please tell me about your character's backstory.") # Send text fallback
        except SlackApiError as e:
            logger.error(f"Slack API error uploading file for {user_id}: {e.response['error']}", exc_info=True)
            say("I found your character sheet, but encountered an error sending it. My apologies. Please tell me about your character's backstory.")
        except Exception as e:
            logger.error(f"Unexpected error uploading file for {user_id}: {e}", exc_info=True)
            say("Something went wrong sending your character sheet. Please tell me about your character's backstory.")

        # Update status and save profile regardless of upload success, as character was parsed.
        profile["creation_status"] = "character_created"
        save_player_profiles()
        logger.info(f"User {user_id} status set to character_created.")
        return True # Indicate overall success (character loaded), even if PDF upload failed

    except Exception as e:
        logger.error(f"Error during PDF parsing or upload process for {user_id}: {e}", exc_info=True)
        # Clean up potentially partially created session
        if user_id in CHARACTER_SESSIONS:
            del CHARACTER_SESSIONS[user_id]
        # Don't change status, let the calling logic handle retry/error message
        return False # Indicate failure


@app.event("message")
def handle_message_events(body: Dict[str, Any], say: Say, logger: Logger, client: WebClient): # Added client
    """Handles direct messages and character creation state machine."""
    event = body["event"]
    user_id = event["user"]

    # --- Handle File Uploads (PDF Character Sheets) ---
    if event.get("files"):
        profile = PLAYER_PROFILES.get(user_id, {})
        status = profile.get("creation_status", "unknown")

        # Only process PDF uploads if character creation is complete
        if status == "character_created":
            for file_info in event["files"]:
                if file_info.get("filetype") == "pdf":
                    logger.info(f"User {user_id} uploaded a PDF file: {file_info.get('name')}")
                    pdf_url = file_info.get("url_private_download")
                    if pdf_url:
                        try:
                            # Download the PDF using requests with auth header
                            headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
                            response = requests.get(pdf_url, headers=headers, stream=True)
                            response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

                            # Save PDF to a temporary file
                            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_pdf:
                                for chunk in response.iter_content(chunk_size=8192):
                                    temp_pdf.write(chunk)
                                temp_pdf_path = temp_pdf.name
                            logger.info(f"Downloaded uploaded PDF to temporary file: {temp_pdf_path}")

                            # Parse the downloaded PDF
                            updated_character = parse_character_pdf(temp_pdf_path)

                            if updated_character:
                                # Update character session
                                CHARACTER_SESSIONS[user_id] = updated_character
                                logger.info(f"Updated character session for user {user_id} from uploaded PDF.")

                                # Update player profile (optional, could just rely on session)
                                profile["creation_data"]["class_key"] = getattr(updated_character, 'class_name', 'fighter').lower()
                                profile["creation_data"]["background"] = getattr(updated_character, 'background', 'Folk Hero')
                                profile["creation_data"]["species"] = getattr(updated_character, 'species', 'Human')
                                profile["creation_data"]["ability_scores"] = {
                                    "strength": getattr(updated_character, 'strength', 10),
                                    "dexterity": getattr(updated_character, 'dexterity', 10),
                                    "constitution": getattr(updated_character, 'constitution', 10),
                                    "intelligence": getattr(updated_character, 'intelligence', 10),
                                    "wisdom": getattr(updated_character, 'wisdom', 10),
                                    "charisma": getattr(updated_character, 'charisma', 10),
                                }
                                # You might want to serialize more data from the object to the profile here
                                save_player_profiles()
                                say("Understood. I've updated your character details based on the sheet you provided.")
                            else:
                                say("Apologies, I couldn't read the character data from the PDF you uploaded. Please ensure it's a compatible character sheet.")

                            # Clean up temporary file
                            try:
                                os.remove(temp_pdf_path)
                                logger.info(f"Removed temporary PDF file: {temp_pdf_path}")
                            except OSError as e:
                                logger.error(f"Error removing temporary PDF file {temp_pdf_path}: {e}")

                        except requests.exceptions.RequestException as e:
                            logger.error(f"Error downloading PDF file for user {user_id}: {e}")
                            say("I couldn't download the PDF you uploaded. Please try again.")
                        except Exception as e:
                            logger.error(f"Error processing uploaded PDF for user {user_id}: {e}", exc_info=True)
                            say("Something went wrong while processing the PDF you uploaded.")
                        finally:
                            # Ensure cleanup even if parsing fails mid-way
                            if 'temp_pdf_path' in locals() and os.path.exists(temp_pdf_path):
                                try: os.remove(temp_pdf_path)
                                except OSError as e: logger.error(f"Error removing temporary PDF file {temp_pdf_path} in finally block: {e}")
                    else:
                        logger.warning(f"No download URL found for file uploaded by user {user_id}.")
            return # Stop further processing if it was a file upload

    # --- Handle Regular Text Messages ---
    # Ignore messages from bots, message changes, or channel joins/leaves (already checked but good safety)
    if event.get("subtype") is not None and event.get("subtype") != "message_deleted":
        logger.debug(f"Ignoring message subtype: {event.get('subtype')}")
        return
    if event.get("bot_id"):
        logger.debug(f"Ignoring message from bot: {event.get('bot_id')}")
        return

    # Process only direct messages (im)
    if event["channel_type"] == "im":
        # user_id = event["user"] # Already defined if not file upload
        message_text = event.get("text", "").strip() # Use get for safety if no text
        message_text_lower = message_text.lower()

        # --- Check if user profile exists, initiate if not ---
        if user_id not in PLAYER_PROFILES:
            logger.info(f"User {user_id} sent DM but has no profile. Initiating creation.")
            initiate_character_creation(user_id, client, logger, say)
            return # Stop processing this message further

        # --- Profile exists, continue normal processing ---
        profile = PLAYER_PROFILES.get(user_id, {}) # Get profile again (though it should exist now)

        # --- Debug Triggers ---
        if message_text_lower == "debug trigger":
            # This will now call the initiation helper
            logger.info(f"Debug trigger activated by {user_id}. Re-initiating.")
            initiate_character_creation(user_id, client, logger, say)
            return
        elif message_text_lower == "debug reset profiles":
            PLAYER_PROFILES.clear()
            save_player_profiles()
            say(text="Debug profiles reset. All player profiles have been cleared.")
            return

        # New riddle-answer handling state: if user is awaiting to solve riddle.
        if profile.get("creation_status") == "awaiting_riddle_answer":
            if message_text_lower == "echo $path":
                PLAYER_PROFILES[user_id]["creation_status"] = "awaiting_persona_selection"
                save_player_profiles()
            # Always call LLM to respond during riddle phase
            response_text = handle_message(message_text, user_id)
            say(text=response_text)
            return

        # --- Get or Create Creation State ---
        # profile = PLAYER_PROFILES.get(user_id, {}) # Already fetched above
        status = profile.get("creation_status", "unknown")

        # If user is not in creation process and no active character session, start it
        # This block is now less likely to be hit due to the check at the start of the DM handler,
        # but kept as a fallback. It should ideally not call initiate_character_creation again.
        if status in ["unknown", "character_created", "needs_welcome", "initiation_failed"] and user_id not in CHARACTER_SESSIONS:
             logger.warning(f"User {user_id} reached state machine start without proper initiation. Attempting persona selection prompt.")
             # Initialize profile for creation with persona selection state (or reset if failed)
             PLAYER_PROFILES[user_id] = {
                 "creation_status": "awaiting_persona_selection",
                 "creation_data": profile.get("creation_data", {}), # Keep existing data if possible
                 "backstory_notes": profile.get("backstory_notes", "")
             }
             profile = PLAYER_PROFILES[user_id]
             status = profile["creation_status"]
             # Retrieve list of unclaimed personas from preset_personas.json
             persona_list = ""
             for p in PRESET_PERSONAS:
                 if not p.get("claimed", False):
                     persona_list += f"• {p['name']} – {p['archetype']}\n"
             reply = f"Welcome to the PATH Variable. Please select one of the following personas by typing its name:\n{persona_list}"
             say(text=reply)
             save_player_profiles()
             return

        # --- Character Creation State Machine ---
        character: Optional[BaseCharacter] = CHARACTER_SESSIONS.get(user_id) # Add type hint
        reply = None # Initialize reply

        try: # Wrap state machine logic
            if status == "awaiting_persona_selection":
                selected_persona = None
                message_text_lower = message_text.lower()

                # First, try flexible substring match on player message
                for persona in PRESET_PERSONAS:
                    if not persona.get("claimed", False) and persona["name"].lower() in message_text_lower:
                        selected_persona = persona
                        break

                if selected_persona:
                    profile["creation_data"]["persona"] = selected_persona
                    # Mark the persona as claimed IN THE MAIN LIST
                    selected_persona["claimed"] = True
                    save_preset_personas() # Save the updated list to JSON

                    # --- Call Helper Function ---
                    success = _instantiate_from_persona_and_send_pdf(
                        user_id, selected_persona, profile, client, event, logger, say
                    )

                    if not success:
                        # Helper function failed, send error message and revert state
                        say("Apologies, I encountered an issue setting up your character from that persona. Let's try selecting again.")
                        profile["creation_status"] = "awaiting_persona_selection" # Revert state
                        # Revert claim status? Maybe not.
                        save_player_profiles()
                    # No need for an 'else' message here, as the helper sends the PDF with comment on success
                    return # End processing for this state

                # If no direct match in user message, call LLM and check ITS response
                else:
                    logger.info(f"No direct persona match in user message '{message_text}'. Querying LLM.")
                    response_text = handle_message(message_text, user_id)
                    llm_selected_persona = None

                    # Parse LLM reply for persona names
                    llm_reply_lower = response_text.lower() if response_text else ""
                    for persona in PRESET_PERSONAS:
                        if not persona.get("claimed", False) and persona["name"].lower() in llm_reply_lower:
                            llm_selected_persona = persona
                            logger.info(f"LLM response indicated selection of persona: {persona['name']}")
                            break

                    if llm_selected_persona:
                        # Persona found in LLM response, update profile and call helper
                        profile["creation_data"]["persona"] = llm_selected_persona
                        llm_selected_persona["claimed"] = True # Mark claimed in the main list
                        save_preset_personas() # Save the claim status

                        # --- Call Helper Function ---
                        success = _instantiate_from_persona_and_send_pdf(
                            user_id, llm_selected_persona, profile, client, event, logger, say
                        )

                        if success:
                            # Send the original LLM response (which should contain confirmation/next prompt)
                            # The helper already sent the PDF with its own comment.
                            say(response_text)
                        else:
                            # Helper function failed after LLM interpretation
                            say("Apologies, I understood your choice but encountered an issue setting up the character. Let's try selecting again.")
                            profile["creation_status"] = "awaiting_persona_selection" # Revert state
                            # Revert claim status? Maybe not.
                            save_player_profiles()
                        return # End processing for this state
                    else:
                        # No persona detected in LLM response either, just send LLM reply
                        say(response_text if response_text else "Could you clarify which persona you'd like?")
                        return

            # This state shouldn't be reached, as _instantiate_from_persona_and_send_pdf advances state directly
            elif status == "awaiting_backstory_input":
                 logger.warning(f"User {user_id} unexpectedly in awaiting_backstory_input state. Treating as character_created.")
                 # Capture backstory anyway
                 existing_notes = profile.get("backstory_notes", "")
                 profile["backstory_notes"] = existing_notes + "\n" + message_text if existing_notes else message_text
                 profile["creation_status"] = "character_created"
                 save_player_profiles()
                 # Pass to general handler
                 response_text = handle_message(message_text, user_id)
                 say(text=response_text)
                 return

            elif status == "character_created": # Changed from creation_complete
                 # Character creation is done, pass to the general message handler for RAG/chat
                 logger.info(f"User {user_id} character creation complete. Passing message to general handler.")
                 response_text = handle_message(message_text, user_id)
                 say(text=response_text)
                 return # Don't save profile status again

            else: # Fallback for unknown status
                 logger.warning(f"User {user_id} in unexpected creation state '{status}'. Passing to general handler.")
                 # If they have an active character session, use general handler
                 if character:
                      response_text = handle_message(message_text, user_id)
                      say(text=response_text)
                      return # Explicitly return after handling the message
                 else: # No character session, restart creation
                      logger.warning("No active character session found, restarting initiation.")
                      initiate_character_creation(user_id, client, logger, say)
                      return

        except Exception as e: # Catch broad exceptions during state machine logic
             logger.error(f"Error during character creation state machine (State: {status}) for user {user_id}: {e}", exc_info=True)
             # Let handle_message explain the error
             response_text = handle_message(f"Error in state {status}: {e}. Please clarify or repeat your last input.", user_id)
             say(text=response_text)
             # Don't save profile here as the state might be inconsistent

    else: # Message not in DM
        logger.debug(f"Ignoring message in channel type: {event['channel_type']}")


# --- Helper to Initiate Character Creation ---
def initiate_character_creation(user_id: str, client: WebClient, logger: Logger, say: Say):
    """Clears session, creates profile, sends welcome and riddle."""
    logger.info(f"Initiating character creation for user {user_id}...")
    # Clear any existing character session for the user
    if user_id in CHARACTER_SESSIONS:
        logger.warning(f"Clearing previous character session for {user_id} during initiation.")
        del CHARACTER_SESSIONS[user_id]

    # Initialize profile for character creation status tracking
    PLAYER_PROFILES[user_id] = {
        "creation_status": "needs_welcome", # Start with this status
        "creation_data": {},
        "backstory_notes": "",
    }
    logger.info(f"Initialized player profile for {user_id}.")

    # Send the initial welcome message via DM
    try:
        welcome_message = """To Whom the World Whispers Differently,

You are cordially invited to board the PATH Variable, departing from World Trade Center at precisely Midnight on the Vernal Conjunction.

This is not a public route. It appears only for those who would change the tracks upon which reality runs.

You have been chosen—not by fate, but by pattern. By deviation. By your refusal to become predictable.

Bring nothing but your mind, your memory, and this token of your character.

Regards,
C.C."""
        # Use client.chat_postMessage for reliable DM sending
        client.chat_postMessage(
            channel=user_id,
            text=welcome_message
        )
        logger.info(f"Sent welcome message to {user_id}")

        # Send riddle using say (as it's context-dependent for DM channel)
        riddles = LORE_DATA.get("riddles", [])
        if riddles:
            riddle = random.choice(riddles)
            say(text="_Solve this riddle to begin your journey:_\n\n" + riddle)
            logger.info(f"Sent riddle to {user_id}")
        else:
            say(text="No riddle found, but proceed with caution.")
            logger.warning("No riddles found in lore data.")

        # Update status and save
        PLAYER_PROFILES[user_id]["creation_status"] = "awaiting_riddle_answer"
        save_player_profiles()
        logger.info(f"User {user_id} status set to awaiting_riddle_answer.")

    except SlackApiError as e:
         logger.error(f"Slack API error sending welcome/riddle to {user_id}: {e.response['error']}")
         # Optionally try to update status anyway or handle differently
         PLAYER_PROFILES[user_id]["creation_status"] = "initiation_failed"
         save_player_profiles()
    except Exception as e:
        logger.error(f"Failed to send welcome message or riddle to {user_id}: {e}", exc_info=True)
        PLAYER_PROFILES[user_id]["creation_status"] = "initiation_failed"
        save_player_profiles()


@app.event("team_join")
def handle_team_join(body: Dict[str, Any], client: WebClient, logger: Logger, say: Say): # Added say
    """Handles new users joining the Slack team and starts character creation."""
    event = body["event"]
    # Check if 'user' key and 'id' subkey exist
    if "user" in event and isinstance(event["user"], dict) and "id" in event["user"]:
        user_id = event["user"]["id"]
        logger.info(f"New user joined the team: {user_id}")
        # Call the helper function to handle initiation
        initiate_character_creation(user_id, client, logger, say)
    else:
        logger.error(f"Received team_join event with unexpected structure: {event}")


# --- Helper to Initiate Character Creation via DM (for scans) ---
def initiate_character_creation_dm(user_id: str, client: WebClient, logger: Logger):
    """Creates profile, sends welcome and riddle via client.chat_postMessage."""
    logger.info(f"Proactively initiating character creation via DM for user {user_id}...")
    # Clear any existing character session for the user
    if user_id in CHARACTER_SESSIONS:
        logger.warning(f"Clearing previous character session for {user_id} during proactive initiation.")
        del CHARACTER_SESSIONS[user_id]

    # Initialize profile for character creation status tracking
    PLAYER_PROFILES[user_id] = {
        "creation_status": "needs_welcome", # Start with this status
        "creation_data": {},
        "backstory_notes": "",
    }
    logger.info(f"Initialized player profile for {user_id}.")

    # Send the initial welcome message via DM
    try:
        welcome_message = """To Whom the World Whispers Differently,

You are cordially invited to board the PATH Variable, departing from World Trade Center at precisely Midnight on the Vernal Conjunction.

This is not a public route. It appears only for those who would change the tracks upon which reality runs.

You have been chosen—not by fate, but by pattern. By deviation. By your refusal to become predictable.

Bring nothing but your mind, your memory, and this token of your character.

Regards,
C.C."""
        client.chat_postMessage(
            channel=user_id,
            text=welcome_message
        )
        logger.info(f"Sent welcome message to {user_id}")

        # Send riddle
        riddles = LORE_DATA.get("riddles", [])
        if riddles:
            riddle = random.choice(riddles)
            client.chat_postMessage(
                channel=user_id,
                text="_Solve this riddle to begin your journey:_\n\n" + riddle
            )
            logger.info(f"Sent riddle to {user_id}")
        else:
            client.chat_postMessage(
                channel=user_id,
                text="No riddle found, but proceed with caution."
            )
            logger.warning("No riddles found in lore data.")

        # Update status and save
        PLAYER_PROFILES[user_id]["creation_status"] = "awaiting_riddle_answer"
        save_player_profiles()
        logger.info(f"User {user_id} status set to awaiting_riddle_answer.")

    except SlackApiError as e:
         # Handle cases like user not found, channel not found, or bot not in channel (though DM should work)
         logger.error(f"Slack API error initiating DM for {user_id}: {e.response['error']}")
         # Remove profile if initiation failed completely
         if user_id in PLAYER_PROFILES:
             del PLAYER_PROFILES[user_id]
             logger.info(f"Removed profile for {user_id} due to DM initiation failure.")
             save_player_profiles() # Save the removal
    except Exception as e:
        logger.error(f"Unexpected error initiating DM for {user_id}: {e}", exc_info=True)
        if user_id in PLAYER_PROFILES:
             del PLAYER_PROFILES[user_id]
             logger.info(f"Removed profile for {user_id} due to unexpected DM initiation error.")
             save_player_profiles() # Save the removal


# --- Function to Scan Users and Initiate ---
def scan_and_initiate_users(client: WebClient, logger: Logger):
    """Scans workspace users and initiates creation for those without profiles."""
    logger.info("Scanning workspace users to initiate character creation...")
    try:
        processed_users = 0
        initiated_count = 0
        cursor = None
        while True: # Handle pagination
            response = client.users_list(limit=200, cursor=cursor) # Adjust limit as needed
            members = response.get("members", [])
            if not members:
                logger.info("No members found in users_list response.")
                break

            for member in members:
                user_id = member.get("id")
                is_bot = member.get("is_bot", False)
                deleted = member.get("deleted", False)
                processed_users += 1

                if user_id and not is_bot and not deleted:
                    if user_id not in PLAYER_PROFILES:
                        logger.info(f"User {user_id} ({member.get('name', 'N/A')}) found without profile. Initiating.")
                        initiate_character_creation_dm(user_id, client, logger)
                        initiated_count += 1
                    # else: # Optional: Log existing users
                    #     logger.debug(f"User {user_id} ({member.get('name', 'N/A')}) already has a profile.")

            cursor = response.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break # Exit loop if no more pages

        logger.info(f"User scan complete. Processed: {processed_users}, Initiated for: {initiated_count} new users.")

    except SlackApiError as e:
        logger.error(f"Slack API error during user scan: {e.response['error']}. Check bot scopes (users:read).")
    except Exception as e:
        logger.error(f"Unexpected error during user scan: {e}", exc_info=True)


# --- Start the App ---
if __name__ == "__main__":
    logger.info("Entering main execution block...") # Added logging
    # Reminder about index building
    if not os.path.exists(rag_retriever.FAISS_INDEX_PATH):
        logger.warning(f"FAISS index not found at {rag_retriever.FAISS_INDEX_PATH}. Ensure build_index.py has been run.")

    # Instantiate WebClient for startup tasks
    try:
        client = WebClient(token=SLACK_BOT_TOKEN)
        logger.info("WebClient initialized for startup tasks.")
        # Perform user scan on startup
        scan_and_initiate_users(client, logger)
    except Exception as e:
        logger.error(f"Failed to initialize WebClient or run startup scan: {e}", exc_info=True)
        # Decide if this is fatal or if the bot should continue
        # sys.exit(1) # Uncomment to make it fatal

    logger.info("Initializing SocketModeHandler...") # Added logging
    try:
        handler = SocketModeHandler(app, SLACK_APP_TOKEN)
        logger.info("SocketModeHandler initialized successfully.") # Added logging
        logger.info("Starting handler...") # Added logging
        handler.start() # Wrapped in try/except
    except Exception as e:
        logger.error(f"Error starting SocketModeHandler: {e}", exc_info=True)
        # Optionally add sys.exit(1) here if this is critical
    finally:
        # This might not be reached if handler.start() blocks indefinitely
        logger.info("Slack chatbot shut down (or handler exited).")
