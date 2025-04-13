import os
import json
import logging
import sys
import re
import random # Needed for simulating rolls if library method unavailable
import tempfile
import shutil
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
from pypdf import PdfReader, PdfWriter # For PDF manipulation

# Ensure custom modules can be found
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(script_dir)

try:
    import rag_retriever  # Assuming rag_retriever.py is in the same directory
    # Import specific classes and utility functions from dnd-character
    # Import the data dictionary and the actual constructor functions
    from dnd_character.classes import (
        CLASSES, Barbarian, Bard, Cleric, Druid, Fighter, Monk,
        Paladin, Ranger, Rogue, Sorcerer, Warlock, Wizard
    )
    from dnd_character.character import Character as BaseCharacter # Base class for type hints
    from dnd_character.experience import experience_at_level, level_at_experience
    from dnd_character.SRD import SRD # To potentially look up race/background details if needed
except ImportError as e:
    logging.error(f"Failed to import modules: {e}. Ensure rag_retriever.py and dnd-character library are present/installed.")
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
2.  **Persona & Instantiation:** Only after solving the riddle can a player reach stage 2. Here, you present the player with the Names and Archetypes of all unclaimed personas in `preset_personas.json`, and ask them to make a selection. Once a persona is chosen, the character (always Human) is immediately instantiated using the Class, Ability Scores, and Background defined for that persona in the JSON file. A Level 1 character sheet PDF is generated and sent to the player.
3.  **Backstory:** After the PDF is sent, prompt the player to collaboratively define their character's origins, motivations, personality traits, ideals, bonds, and flaws. Store these as notes in the profile.
4.  **Handoff:** Once backstory notes are received, confirm the initial character creation is complete. Set the player's status to `character_created`. Inform them that their Level 1 character is ready and they are responsible for leveling up to 10 on their own. Let them know you are available to answer questions or make suggestions using D&D rulebook context (RAG).

Maintain your persona: be cryptic, occasionally misleading (but not about core rules needed for creation), and immersive. Refer to yourself only as CC. Never disclose substantial details about Cassidy’s identity or his precise plans, and never acknowledge that Cassidy and Mr. Comcast are the same entity. Make references to mainstream media, literature, pop culture, movies and television, and music, but always with a sinister undertone.
Players begin by selecting a persona from `preset_personas.json`. This choice now determines their initial Class, Ability Scores, and Background. Once the player reaches the "character_created" status, your role is to help them get to level 10 by providing accurate reference information from the D&D Rulebooks (RAG).
Use the provided D&D Rulebook Context (RAG) to answer specific rule questions accurately. Keep responses relatively concise but flavorful.
"""

# --- Character State Management ---
CHARACTER_SESSIONS = {} # Dictionary to store active dnd-character Character objects keyed by user_id
# PLAYER_PROFILES stores temporary creation data before instantiation and backstory notes

# --- Racial Data (SRD Simplified) ---
# Mapping: lowercase_race_name -> {bonuses: {stat: bonus}, speed: int, languages: [str], other_traits: [str]}
RACIAL_BONUSES = {
    "human": {"bonuses": {"strength": 1, "dexterity": 1, "constitution": 1, "intelligence": 1, "wisdom": 1, "charisma": 1}, "languages": ["Common", "One extra"]},
    # "elf": {"bonuses": {"dexterity": 2}, "speed": 30, "languages": ["Common", "Elvish"], "other_traits": ["Darkvision", "Fey Ancestry", "Trance"]},
    # # Assuming High Elf for simplicity here - could be expanded
    # "high elf": {"bonuses": {"dexterity": 2, "intelligence": 1}, "speed": 30, "languages": ["Common", "Elvish", "One extra"], "other_traits": ["Darkvision", "Fey Ancestry", "Trance", "Elf Weapon Training", "Cantrip (Wizard)"]},
    # "dwarf": {"bonuses": {"constitution": 2}, "speed": 25, "languages": ["Common", "Dwarvish"], "other_traits": ["Darkvision", "Dwarven Resilience", "Dwarven Combat Training", "Tool Proficiency"]},
    # # Assuming Hill Dwarf
    # "hill dwarf": {"bonuses": {"constitution": 2, "wisdom": 1}, "speed": 25, "languages": ["Common", "Dwarvish"], "other_traits": ["Darkvision", "Dwarven Resilience", "Dwarven Combat Training", "Tool Proficiency", "Dwarven Toughness"]},
    # "halfling": {"bonuses": {"dexterity": 2}, "speed": 25, "languages": ["Common", "Halfling"], "other_traits": ["Lucky", "Brave", "Halfling Nimbleness"]},
    # # Assuming Lightfoot Halfling
    # "lightfoot halfling": {"bonuses": {"dexterity": 2, "charisma": 1}, "speed": 25, "languages": ["Common", "Halfling"], "other_traits": ["Lucky", "Brave", "Halfling Nimbleness", "Naturally Stealthy"]},
    # "dragonborn": {"bonuses": {"strength": 2, "charisma": 1}, "speed": 30, "languages": ["Common", "Draconic"], "other_traits": ["Draconic Ancestry", "Breath Weapon", "Damage Resistance"]},
    # "gnome": {"bonuses": {"intelligence": 2}, "speed": 25, "languages": ["Common", "Gnomish"], "other_traits": ["Darkvision", "Gnome Cunning"]},
    # # Assuming Rock Gnome
    # "rock gnome": {"bonuses": {"intelligence": 2, "constitution": 1}, "speed": 25, "languages": ["Common", "Gnomish"], "other_traits": ["Darkvision", "Gnome Cunning", "Artificer's Lore", "Tinker"]},
    # "half-elf": {"bonuses": {"charisma": 2}, "speed": 30, "languages": ["Common", "Elvish", "One extra"], "other_traits": ["Darkvision", "Fey Ancestry", "Skill Versatility (Choose 2 skills)"]}, # Note: Skill Versatility needs handling
    # "half-orc": {"bonuses": {"strength": 2, "constitution": 1}, "speed": 30, "languages": ["Common", "Orc"], "other_traits": ["Darkvision", "Menacing", "Relentless Endurance", "Savage Attacks"]},
    # "tiefling": {"bonuses": {"intelligence": 1, "charisma": 2}, "speed": 30, "languages": ["Common", "Infernal"], "other_traits": ["Darkvision", "Hellish Resistance", "Infernal Legacy"]},
}
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


# --- PDF Helper Functions ---

# Mapping from dnd-character skill names to PDF field prefixes and associated ability
SKILL_PDF_MAP = {
    "acrobatics": ("acro", "dexterity"),
    "animal-handling": ("anhan", "wisdom"), # Note: PDF uses 'anhan', library uses 'animal-handling'
    "arcana": ("arcana", "intelligence"),
    "athletics": ("ath", "strength"),
    "deception": ("decep", "charisma"),
    "history": ("hist", "intelligence"),
    "insight": ("insight", "wisdom"), # Corrected tuple syntax
    "intimidation": ("intim", "charisma"),
    "investigation": ("invest", "intelligence"),
    "medicine": ("med", "wisdom"),
    "nature": ("nature", "intelligence"),
    "perception": ("per", "wisdom"),
    "performance": ("perf", "charisma"),
    "persuasion": ("pers", "charisma"),
    "religion": ("relig", "intelligence"),
    "sleight-of-hand": ("soh", "dexterity"), # Note: PDF uses 'soh', library uses 'sleight-of-hand'
    "stealth": ("stealth", "dexterity"),
    "survival": ("surv", "wisdom"),
}

ABILITY_SKILL_MAP = {
    "strength": ["athletics"],
    "dexterity": ["acrobatics", "sleight-of-hand", "stealth"],
    "intelligence": ["arcana", "history", "investigation", "nature", "religion"],
    "wisdom": ["animal-handling", "insight", "medicine", "perception", "survival"],
    "charisma": ["deception", "intimidation", "performance", "persuasion"],
}


def get_skill_modifier(character: BaseCharacter, skill_name: str) -> int:
    """Calculates the modifier for a given skill."""
    if skill_name not in SKILL_PDF_MAP:
        return 0 # Should not happen with internal calls

    _, ability_name = SKILL_PDF_MAP[skill_name]
    ability_score = getattr(character, ability_name, 10)
    modifier = BaseCharacter.get_ability_modifier(ability_score) # Use BaseCharacter alias

    # Check proficiency
    is_proficient = False
    skill_dict_name = f"skills_{ability_name}"
    if hasattr(character, skill_dict_name):
        skill_dict = getattr(character, skill_dict_name, {})
        # Use the library's internal skill name format (e.g., 'animal-handling')
        is_proficient = skill_dict.get(skill_name, False)

    if is_proficient:
        modifier += getattr(character, 'prof_bonus', 2)

    return modifier

def get_save_modifier(character: BaseCharacter, ability_name: str) -> int:
    """Calculates the saving throw modifier for a given ability."""
    ability_score = getattr(character, ability_name, 10)
    modifier = BaseCharacter.get_ability_modifier(ability_score) # Use BaseCharacter alias
    if ability_name in getattr(character, 'saving_throws', []):
        modifier += getattr(character, 'prof_bonus', 2)
    return modifier

def format_profs_langs(character: BaseCharacter, profile_data: Dict[str, Any]) -> str: # Added profile_data
    """Formats proficiencies and languages into a single string."""
    profs = []
    # Access the proficiencies dictionary directly
    if hasattr(character, 'proficiencies') and character.proficiencies:
        # Extract names from the proficiency details
        profs.extend([details.get('name', index) for index, details in character.proficiencies.items()])

    # Get languages from racial traits stored in profile if available
    racial_info = profile_data.get("racial_traits_info", {})
    langs = racial_info.get("languages", ["Common"]) # Default to Common

    # Add background languages if tracked separately (not currently)

    return f"Proficiencies: {', '.join(profs) if profs else 'None'}\nLanguages: {', '.join(langs) if langs else 'None'}"

def format_equipment(character: BaseCharacter) -> str:
    """Formats equipment into a string."""
    if not hasattr(character, 'inventory') or not character.inventory:
        return "None"
    # Inventory items are _Item objects
    items = []
    for item in character.inventory:
        # _Item objects have a 'name' attribute
        name = getattr(item, 'name', 'Unknown Item')
        quantity = getattr(item, 'quantity', 1)
        items.append(f"{name}{f' (x{quantity})' if quantity > 1 else ''}")
    return "\n".join(f"- {item}" for item in items)

def format_features(character: BaseCharacter, profile_data: Dict[str, Any]) -> str: # Added profile_data
    """Formats features and traits into a string."""
    features = []
    # Class features are stored in class_features dict
    if hasattr(character, 'class_features') and character.class_features:
        # Extract names from the feature details
        features.extend([details.get('name', index) for index, details in character.class_features.items()])

    # Get other racial traits from profile data
    racial_info = profile_data.get("racial_traits_info", {})
    other_traits = racial_info.get("other_traits", [])
    features.extend(other_traits)

    # Add background features if tracked separately (not currently)

    return "\n".join(f"- {feature}" for feature in features) if features else "None"


def fill_character_sheet(character: BaseCharacter, profile_data: Dict[str, Any], blank_pdf_path: str, output_pdf_path: str, logger: Logger) -> bool: # Added profile_data
    """Fills a character sheet PDF with character data."""
    try:
        reader = PdfReader(blank_pdf_path)
        writer = PdfWriter()
        writer.append(reader) # Copy pages from reader to writer

        fields = reader.get_fields()
        if not fields:
            logger.error(f"No form fields found in blank PDF: {blank_pdf_path}")
            return False

        # Define the mapping from PDF field names to character attributes/methods
        # Using lambdas for dynamic values or calculations based on character.py
        pdf_field_map = {
            'CharacterName': lambda c: getattr(c, 'name', ''),
            'ClassLevel': lambda c: f"{getattr(c, 'class_name', 'N/A')} {getattr(c, 'level', 1)}",
            'Race': lambda c: getattr(c, 'species', ''),
            'Background': lambda c: getattr(c, 'background', ''),
            'Alignment': lambda c: getattr(c, 'alignment', ''),
            'ExperiencePoints': lambda c: str(c.experience), # Access experience property
            'STRscore': lambda c: str(getattr(c, 'strength', 10)),
            'DEXscore': lambda c: str(getattr(c, 'dexterity', 10)),
            'CONscore': lambda c: str(getattr(c, 'constitution', 10)),
            'INTscore': lambda c: str(getattr(c, 'intelligence', 10)),
            'WISscore': lambda c: str(getattr(c, 'wisdom', 10)),
            'CHAscore': lambda c: str(getattr(c, 'charisma', 10)),
            'STRbonus': lambda c: str(BaseCharacter.get_ability_modifier(getattr(c, 'strength', 10))), # Use BaseCharacter alias
            'DEXbonus': lambda c: str(BaseCharacter.get_ability_modifier(getattr(c, 'dexterity', 10))), # Use BaseCharacter alias
            'CONbonus': lambda c: str(BaseCharacter.get_ability_modifier(getattr(c, 'constitution', 10))), # Use BaseCharacter alias
            'INTbonus': lambda c: str(BaseCharacter.get_ability_modifier(getattr(c, 'intelligence', 10))), # Use BaseCharacter alias
            'WISbonus': lambda c: str(BaseCharacter.get_ability_modifier(getattr(c, 'wisdom', 10))), # Use BaseCharacter alias
            'CHAbonus': lambda c: str(BaseCharacter.get_ability_modifier(getattr(c, 'charisma', 10))), # Use BaseCharacter alias
            'STRsave': lambda c: str(get_save_modifier(c, 'strength')),
            'DEXsave': lambda c: str(get_save_modifier(c, 'dexterity')),
            'CONsave': lambda c: str(get_save_modifier(c, 'constitution')),
            'INTsave': lambda c: str(get_save_modifier(c, 'intelligence')),
            'WISsave': lambda c: str(get_save_modifier(c, 'wisdom')),
            'CHAsave': lambda c: str(get_save_modifier(c, 'charisma')),
            'STRsavePROF': lambda c: '/Yes' if 'strength' in getattr(c, 'saving_throws', []) else '/Off',
            'DEXsavePROF': lambda c: '/Yes' if 'dexterity' in getattr(c, 'saving_throws', []) else '/Off',
            'CONsavePROF': lambda c: '/Yes' if 'constitution' in getattr(c, 'saving_throws', []) else '/Off',
            'INTsavePROF': lambda c: '/Yes' if 'intelligence' in getattr(c, 'saving_throws', []) else '/Off',
            'WISsavePROF': lambda c: '/Yes' if 'wisdom' in getattr(c, 'saving_throws', []) else '/Off',
            'CHAsavePROF': lambda c: '/Yes' if 'charisma' in getattr(c, 'saving_throws', []) else '/Off',
            'HPMax': lambda c: str(getattr(c, 'max_hp', 1)),
            'CurrentHP': lambda c: str(getattr(c, 'current_hp', 1)),
            'TempHP': lambda c: str(getattr(c, 'temp_hp', 0)),
            'HitDiceTotal': lambda c: f"{getattr(c, 'level', 1)}d{getattr(c, 'hd', 8)}",
            # Use ACworn field as identified in the PDF inspection
            'ACworn': lambda c: str(getattr(c, 'armor_class', 10)),
            'Init': lambda c: str(BaseCharacter.get_ability_modifier(getattr(c, 'dexterity', 10))), # Use BaseCharacter alias
            'Speed': lambda c: str(getattr(c, 'speed', 30)),
            'ProfBonus': lambda c: str(getattr(c, 'prof_bonus', 2)),
            # Passive Wisdom (Perception)
            'PWP': lambda c: str(10 + get_skill_modifier(c, 'perception')),
            'ProfsLangs': lambda c: format_profs_langs(c, profile_data), # Pass profile_data
            'Equipment': format_equipment,
            'FeaturesTraits': lambda c: format_features(c, profile_data), # Pass profile_data
            'PersonalityTraits': lambda c: getattr(c, 'personality', ''), # Mapped to 'personality'
            'Ideals': lambda c: getattr(c, 'ideals', ''),
            'Bonds': lambda c: getattr(c, 'bonds', ''),
            'Flaws': lambda c: getattr(c, 'flaws', ''),
            # Wealth - convert detailed dict back to simple string for PDF
            'Copper': lambda c: str(getattr(c, 'wealth_detailed', {}).get('cp', 0)),
            'Silver': lambda c: str(getattr(c, 'wealth_detailed', {}).get('sp', 0)),
            'Electrum': lambda c: str(getattr(c, 'wealth_detailed', {}).get('ep', 0)),
            'Gold': lambda c: str(getattr(c, 'wealth_detailed', {}).get('gp', 0)),
            'Platinum': lambda c: str(getattr(c, 'wealth_detailed', {}).get('pp', 0)),
        }

        # Add skill fields dynamically based on SKILL_PDF_MAP
        for lib_skill_name, (pdf_prefix, ability_name) in SKILL_PDF_MAP.items():
            # Skill Modifier Field (e.g., 'Acrobatics') - Capitalize PDF field name
            pdf_skill_field_name = lib_skill_name.replace('-', ' ').title().replace(' ', '') # e.g. AnimalHandling
            # Handle edge cases like SleightOfHand
            if lib_skill_name == 'sleight-of-hand': pdf_skill_field_name = 'SleightofHand'

            pdf_field_map[pdf_skill_field_name] = lambda c, sn=lib_skill_name: str(get_skill_modifier(c, sn))

            # Skill Proficiency Field (e.g., 'acroPROF')
            prof_field_name = f"{pdf_prefix}PROF"
            pdf_field_map[prof_field_name] = lambda c, sn=lib_skill_name, an=ability_name: \
                '/Yes' if getattr(c, f"skills_{an}", {}).get(sn, False) else '/Off'

            # Skill Expertise Field (e.g., 'acroEXP') - Assume '/Off' for Level 1
            exp_field_name = f"{pdf_prefix}EXP"
            pdf_field_map[exp_field_name] = lambda c: '/Off'


        update_dict = {}
        missing_fields = []
        for field_name, value_getter in pdf_field_map.items():
            if field_name in fields:
                try:
                    # Ensure value_getter is callable
                    if callable(value_getter):
                         value = value_getter(character)
                    else:
                         # This case should ideally not happen with the lambda approach
                         logger.warning(f"Non-callable value getter for field '{field_name}'")
                         value = '' # Default to empty string

                    # Ensure value is string for text fields (/Tx) or correct type for others (/Btn, /Ch)
                    field_type = fields[field_name].get('/FT')
                    if field_type == '/Tx':
                        value_str = str(value) if value is not None else ''
                    elif field_type == '/Btn':
                         # Ensure it's '/Yes' or '/Off' (or potentially other valid button states)
                         value_str = value if value in ['/Yes', '/Off'] else '/Off'
                    elif field_type == '/Ch':
                         # Choice fields might need specific values, but string conversion is often okay
                         value_str = str(value) if value is not None else ''
                    else: # Default to string conversion
                         value_str = str(value) if value is not None else ''

                    update_dict[field_name] = value_str
                    # logger.debug(f"Mapping field '{field_name}' ({field_type}) to value '{value_str}'") # Optional debug log
                except AttributeError as e:
                    logger.warning(f"Attribute error getting value for field '{field_name}': {e}")
                except Exception as e:
                    logger.error(f"Error getting value for field '{field_name}': {e}")
            else:
                 missing_fields.append(field_name)

        if missing_fields:
            logger.warning(f"Mapped fields not found in PDF '{blank_pdf_path}': {', '.join(missing_fields)}")

        # Update fields in the writer object
        # Need to update fields page by page if the PDF has multiple pages with forms
        for page_num in range(len(writer.pages)):
             writer.update_page_form_field_values(writer.pages[page_num], update_dict)

        # Write the output file
        with open(output_pdf_path, "wb") as output_stream:
            writer.write(output_stream)

        logger.info(f"Successfully filled character sheet: {output_pdf_path}")
        return True

    except FileNotFoundError:
        logger.error(f"Blank PDF not found: {blank_pdf_path}")
        return False
    except Exception as e:
        logger.error(f"Failed to fill PDF character sheet: {e}", exc_info=True)
        return False

# --- Helper Function for Persona Instantiation & PDF ---
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
    Instantiates character from persona data, generates PDF, uploads it,
    and updates profile status. Returns True on success, False on failure.
    """
    logger.info(f"Instantiating character for {user_id} from persona '{selected_persona.get('name', 'Unknown')}'.")

    try:
        # Retrieve data from persona
        class_key = selected_persona.get("class", "fighter").lower()
        ability_scores_list = selected_persona.get("ability_scores", [15, 14, 13, 12, 10, 8])
        background_name = selected_persona.get("background", "Folk Hero")

        # Assign scores (Placeholder: default order)
        default_assignment_order = ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"]
        if len(ability_scores_list) == 6:
            assigned_scores = dict(zip(default_assignment_order, sorted(ability_scores_list, reverse=True)))
            logger.warning(f"Used default score assignment for {user_id}. Update JSON for specifics.")
        else:
            logger.error(f"Invalid ability_scores list length in persona '{selected_persona.get('name', 'Unknown')}'. Falling back.")
            assigned_scores = {"strength": 15, "dexterity": 14, "constitution": 13, "intelligence": 12, "wisdom": 10, "charisma": 8}

        # Store initial data in profile
        profile["creation_data"]["class_key"] = class_key
        profile["creation_data"]["background"] = background_name
        profile["creation_data"]["ability_scores"] = assigned_scores
        profile["creation_data"]["species"] = "Human"

        # Apply Racial Bonuses (Human: +1 all)
        adjusted_scores = {stat: score + 1 for stat, score in assigned_scores.items()}
        racial_info = RACIAL_BONUSES.get("human", {})
        racial_traits_for_pdf = {}
        if "languages" in racial_info: racial_traits_for_pdf["languages"] = racial_info["languages"]
        profile["creation_data"]["racial_traits_info"] = racial_traits_for_pdf
        logger.info(f"Applied Human racial bonus for {user_id}. Adjusted scores: {adjusted_scores}")

        # Class Constructor Lookup
        CONSTRUCTOR_MAP = {
            "barbarian": Barbarian, "bard": Bard, "cleric": Cleric, "druid": Druid,
            "fighter": Fighter, "monk": Monk, "paladin": Paladin, "ranger": Ranger,
            "rogue": Rogue, "sorcerer": Sorcerer, "warlock": Warlock, "wizard": Wizard
        }
        CharacterConstructor = CONSTRUCTOR_MAP.get(class_key)
        if CharacterConstructor is None:
            logger.error(f"Invalid class key '{class_key}' from persona. Defaulting to Fighter.")
            CharacterConstructor = Fighter
            profile["creation_data"]["class_key"] = "fighter"

        # Instantiate Character
        init_args = {
            "name": selected_persona.get("name"), "species": "Human", "background": background_name, **adjusted_scores
        }
        init_args = {k: v for k, v in init_args.items() if v is not None}
        new_character = CharacterConstructor(**init_args)
        CHARACTER_SESSIONS[user_id] = new_character

        # Generate and Upload PDF
        blank_sheet_rel_path = "Blank Sheets/D&D 5e FormFillable Calculating Charsheet1.7 StatBig Multiclass.pdf"
        blank_sheet_abs_path = os.path.normpath(os.path.join(script_dir, "..", blank_sheet_rel_path))
        temp_dir = tempfile.mkdtemp()
        char_name_safe = re.sub(r'[\\/*?:"<>|]', "", getattr(new_character, 'name', 'character'))
        output_filename = f"{char_name_safe}_{user_id}_L1.pdf"
        output_pdf_path = os.path.join(temp_dir, output_filename)

        logger.info(f"Attempting to fill PDF: {blank_sheet_abs_path} -> {output_pdf_path}")
        fill_success = fill_character_sheet(new_character, profile["creation_data"], blank_sheet_abs_path, output_pdf_path, logger)

        pdf_comment = f"Excellent choice: {selected_persona.get('name', 'Unknown')} – {selected_persona.get('archetype', 'N/A')}.\n\nYour Level 1 character sheet is attached, based on the chosen persona. Now, tell me about their origins, motivations, flaws... their story."

        if fill_success:
            try:
                logger.info(f"Uploading filled PDF for user {user_id}")
                response = client.files_upload_v2(
                    channel=event["channel"], file=output_pdf_path, title=f"{getattr(new_character, 'name', 'Character')} - Level 1 Sheet", initial_comment=pdf_comment,
                )
                if response.get("ok"):
                    logger.info(f"Successfully uploaded PDF for user {user_id}")
                else:
                    logger.error(f"Slack API error uploading file for {user_id}: {response.get('error', 'Unknown error')}")
                    say("I generated your character sheet, but couldn't seem to send it. Apologies. Please tell me about your character's backstory.") # Send text fallback
            except SlackApiError as e:
                logger.error(f"Slack API error uploading file for {user_id}: {e.response['error']}", exc_info=True)
                say("I generated your character sheet, but encountered an error sending it. My apologies. Please tell me about your character's backstory.")
            except Exception as e:
                logger.error(f"Unexpected error uploading file for {user_id}: {e}", exc_info=True)
                say("Something went wrong sending your character sheet. Please tell me about your character's backstory.")
            finally:
                try: shutil.rmtree(temp_dir)
                except Exception as e: logger.error(f"Failed to clean up temp dir {temp_dir}: {e}")
        else:
            say("I couldn't generate the character sheet PDF, apologies. Let's proceed anyway. Please tell me about your character's backstory.")

        # Update Status and Save Profile
        profile["creation_status"] = "awaiting_backstory_input"
        save_player_profiles()
        return True # Indicate success

    except Exception as e:
        logger.error(f"Error during persona instantiation/PDF process for {user_id}: {e}", exc_info=True)
        # Clean up potentially partially created session
        if user_id in CHARACTER_SESSIONS:
            del CHARACTER_SESSIONS[user_id]
        # Don't change status, let the calling logic handle retry/error message
        return False # Indicate failure


@app.event("message")
def handle_message_events(body: Dict[str, Any], say: Say, logger: Logger, client: WebClient): # Added client
    """Handles direct messages and character creation state machine."""
    event = body["event"]
    # Ignore messages from bots, message changes, or channel joins/leaves
    if event.get("subtype") is not None and event.get("subtype") != "message_deleted":
        logger.debug(f"Ignoring message subtype: {event.get('subtype')}")
        return
    if event.get("bot_id"):
        logger.debug(f"Ignoring message from bot: {event.get('bot_id')}")
        return

    # Process only direct messages (im)
    if event["channel_type"] == "im":
        user_id = event["user"]
        message_text = event["text"].strip()
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
            # if user_id in CHARACTER_SESSIONS: # Old logic commented out
            #     del CHARACTER_SESSIONS[user_id]
            # # Send welcome message and set status to awaiting_riddle_answer.
            # PLAYER_PROFILES[user_id] = {
            #     "creation_status": "awaiting_riddle_answer",
            #     "creation_data": {},
            #     "backstory_notes": ""
            # }
            # save_player_profiles()
            # profile = PLAYER_PROFILES.get(user_id, {})
            # welcome_message = """To Whom the World Whispers Differently,

# You are cordially invited to board the PATH Variable, departing from World Trade Center at precisely Midnight on the Vernal Conjunction.

# This is not a public route. It appears only for those who would change the tracks upon which reality runs.

# You have been chosen—not by fate, but by pattern. By deviation. By your refusal to become predictable.

# Bring nothing but your mind, your memory, and this token of your character.

# Regards,
# C.C."""
            # say(text=welcome_message)
            # riddles = LORE_DATA.get("riddles", [])
            # import random
            # if riddles:
            #     riddle = random.choice(riddles)
            #     say(text="_Solve this riddle to begin your journey:_\n\n" + riddle)
            # else:
            #     say(text="No riddle found, but proceed with caution.")
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
        if status in ["unknown", "creation_complete", "needs_welcome", "initiation_failed"] and user_id not in CHARACTER_SESSIONS:
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
                            # Revert claim status? Maybe not, user might retry same persona.
                            save_player_profiles()
                        return # End processing for this state
                    else:
                        # No persona detected in LLM response either, just send LLM reply
                        say(response_text if response_text else "Could you clarify which persona you'd like?")
                        return

            # --- COMMENTED OUT ORIGINAL CREATION STEPS ---
            # elif status == "awaiting_concept":
            #     # Characters are always Human. Attempt to parse Class only.
            #     # race_match = re.search(r"\b(human|elf|dwarf|halfling|dragonborn|gnome|half-elf|half-orc|tiefling)\b", message_text_lower) # Commented out race search
            #     found_class_key = None
            #     for class_key_iter in CLASSES.keys():
            #         # Use word boundaries to avoid partial matches (e.g., 'fighter' in 'firefighter')
            #         if re.search(rf"\b{class_key_iter}\b", message_text_lower, re.IGNORECASE):
            #             found_class_key = class_key_iter
            #             break
            #
            #     # Initialize creation_data if missing
            #     if "creation_data" not in profile or not isinstance(profile["creation_data"], dict):
            #         profile["creation_data"] = {}
            #
            #     # # Update race if found - No longer needed, race is always Human
            #     # if race_match:
            #     #     race_name = race_match.group(1).capitalize()
            #     #     profile["creation_data"]["species"] = race_name
            #
            #     # Update class if found and set species to Human
            #     if found_class_key:
            #         profile["creation_data"]["class_key"] = found_class_key
            #         profile["creation_data"]["species"] = "Human" # Automatically set species
            #         logger.info(f"Stored concept for {user_id}: Race=Human, ClassKey={found_class_key}")
            #
            #         # Proceed to ability score method selection
            #         class_name_display = found_class_key.capitalize()
            #         reply = f"A Human {class_name_display}... versatile, indeed. Now, for the essence – the ability scores. Shall we use the Standard Array (15, 14, 13, 12, 10, 8) or tempt fate with rolled scores (4d6 drop lowest)?"
            #         profile["creation_status"] = "awaiting_ability_method"
            #         save_player_profiles()
            #         response_text = handle_message(message_text, user_id) # Let LLM respond with the prompt
            #         say(text=response_text)
            #         return
            #     else:
            #         # Class not found, prompt only for class
            #         save_player_profiles() # Save any potential partial data (though unlikely here)
            #         prompt = "Please provide your character's Class (e.g., Fighter, Wizard, Rogue, etc.). Remember, all adventurers aboard the PATH Variable begin as Human."
            #         say(prompt)
            #         return
            #
            # elif status == "awaiting_ability_method":
            #      if "standard" in message_text_lower or "array" in message_text_lower:
            #          scores = [15, 14, 13, 12, 10, 8]
            #          profile["creation_data"]["ability_method"] = "standard"
            #          profile["creation_data"]["ability_scores_to_assign"] = scores
            #          scores_str = ", ".join(map(str, scores))
            #          reply = f"The Standard Array it is: {scores_str}. Predictable, yet reliable. Now, assign these numbers to Strength, Dexterity, Constitution, Intelligence, Wisdom, and Charisma. How will you distribute this potential? (e.g., 'STR 15, DEX 14, CON 13, INT 10, WIS 12, CHA 8')"
            #          profile["creation_status"] = "awaiting_ability_assignment"
            #          save_player_profiles() # Save state change
            #          response_text = handle_message(message_text, user_id)
            #          say(text=response_text)
            #          return
            #      elif "roll" in message_text_lower or "4d6" in message_text_lower:
            #          # Simulate rolling 4d6 drop lowest
            #          import random
            #          rolls = []
            #          for _ in range(6):
            #              dice = sorted([random.randint(1, 6) for _ in range(4)], reverse=True)
            #              rolls.append(sum(dice[:3]))
            #          rolled_scores = sorted(rolls, reverse=True)
            #          logger.info(f"Rolled scores for {user_id}: {rolled_scores}")
            #
            #          profile["creation_data"]["ability_method"] = "rolled"
            #          profile["creation_data"]["ability_scores_to_assign"] = rolled_scores
            #          scores_str = ", ".join(map(str, rolled_scores))
            #          profile["creation_status"] = "awaiting_ability_assignment"
            #          save_player_profiles() # Save state change
            #          response_text = handle_message(message_text, user_id)
            #          say(text=response_text)
            #          return
            #      else:
            #          response_text = handle_message(message_text, user_id)
            #          say(text=response_text)
            #          return
            #
            # elif status == "awaiting_ability_assignment":
            #     # Parse player's message for explicit assignment
            #     assignments = {}
            #     pattern = re.compile(
            #         r"^\s*(?:\d+\.\s*)?"  # Optional leading number (e.g., "1. ")
            #         r"(?:[-_]{1,2})?"  # Optional opening markdown (e.g., "**")
            #         r"(?:(?:Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma)\s+\()?"  # Optional full name (e.g., "Strength (")
            #         r"\b(STR|DEX|CON|INT|WIS|CHA)\b"  # Capture the abbreviation (STR, DEX, etc.)
            #         r"(?:\))?"  # Optional closing parenthesis
            #         # r"(?:[*_]{1,2})?"  # Optional closing markdown for name/abbr
            #         r"\s*[:=]{1,2}\s*"  # Separator (:, ::, =) surrounded by optional space
            #         r"(?:[*_]{1,2})?"  # Optional markdown around score
            #         r"(\d+)"  # Capture the score
            #         r".*",  # Match rest of the line (description, etc.)
            #     )
            #     matches = pattern.findall(message_text)
            #     valid_parse = True
            #     assigned_scores_list = []
            #
            #     if len(matches) == 6:
            #         for stat, score_str in matches:
            #             try:
            #                 score = int(score_str)
            #                 if 3 <= score <= 20:
            #                     assignments[stat.upper()] = score
            #                     assigned_scores_list.append(score)
            #                 else:
            #                     valid_parse = False
            #                     reply = f"Score {score} for {stat.upper()} seems unusual. Scores typically range from 3 to 20."
            #                     break
            #             except ValueError:
            #                 valid_parse = False
            #                 reply = f"Couldn't understand the score '{score_str}' for {stat.upper()}."
            #                 break
            #     else:
            #         valid_parse = False
            #
            #     # If explicit assignment parse failed, check if user approved LLM suggestion
            #     approval_phrases = ["yes", "yep", "sure", "sounds good", "looks good", "okay", "ok", "alright", "fine", "do it", "go ahead"]
            #     approved = any(phrase in message_text.lower() for phrase in approval_phrases)
            #     suggestion_available = "suggested_ability_scores" in profile.get("creation_data", {})
            #
            #     if (not valid_parse or len(matches) != 6) and approved and suggestion_available:
            #         # Use the saved suggestion
            #         assignments = profile["creation_data"]["suggested_ability_scores"]
            #         assigned_scores_list = list(assignments.values())
            #         valid_parse = True
            #         reply = None  # Clear any previous reply
            #     # Call LLM to get suggestion or clarification
            #     llm_response = handle_message(message_text, user_id)
            #
            #     # Parse LLM reply for suggested assignment - Improved Regex
            #     # Handles formats like: "1. **Strength (STR):** 15 - Description" or "CHA: 8"
            #     suggestion_pattern = re.compile(
            #         r"^\s*(?:\d+\.\s*)?"  # Optional leading number (e.g., "1. ")
            #         r"(?:[-_]{1,2})?"  # Optional opening markdown (e.g., "**")
            #         r"(?:(?:Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma)\s+\()?"  # Optional full name (e.g., "Strength (")
            #         r"\b(STR|DEX|CON|INT|WIS|CHA)\b"  # Capture the abbreviation (STR, DEX, etc.)
            #         r"(?:\))?"  # Optional closing parenthesis
            #         # r"(?:[*_]{1,2})?"  # Optional closing markdown for name/abbr
            #         r"\s*[:=]{1,2}\s*"  # Separator (:, ::, =) surrounded by optional space
            #         r"(?:[*_]{1,2})?"  # Optional markdown around score
            #         r"(\d+)"  # Capture the score
            #         r".*",  # Match rest of the line (description, etc.)
            #         re.IGNORECASE | re.MULTILINE
            #     )
            #     suggestion_matches = suggestion_pattern.findall(llm_response)
            #     suggested_scores = {}
            #     # Check if we found 6 unique stats
            #     if len(set(match[0].upper() for match in suggestion_matches)) == 6:
            #         try:
            #             for stat, score_str in suggestion_matches:
            #                 score = int(score_str)
            #                 suggested_scores[stat.upper()] = score
            #             # Save suggestion in profile
            #             profile.setdefault("creation_data", {})["suggested_ability_scores"] = suggested_scores
            #             save_player_profiles()
            #         except Exception:
            #             pass  # Ignore parse errors, don't save suggestion
            #
            #     if valid_parse and len(assignments) == 6:
            #         # Validate against stored scores
            #         scores_to_assign = sorted(profile["creation_data"].get("ability_scores_to_assign", []), reverse=True)
            #         if sorted(assigned_scores_list, reverse=True) != scores_to_assign:
            #             scores_str = ", ".join(map(str, scores_to_assign))
            #             reply = f"That assignment doesn't seem right. Ensure you use all six scores ({scores_str}) exactly once. Use the format: STR 15, DEX 14, CON 13, INT 10, WIS 12, CHA 8"
            #         else:
            #             # Store assignments in profile
            #             profile["creation_data"]["ability_scores"] = {
            #                 "strength": assignments.get("STR", 0),
            #                 "dexterity": assignments.get("DEX", 0),
            #                 "constitution": assignments.get("CON", 0),
            #                 "intelligence": assignments.get("INT", 0),
            #                 "wisdom": assignments.get("WIS", 0),
            #                 "charisma": assignments.get("CHA", 0),
            #             }
            #             logger.info(f"Stored ability scores for {user_id}: {profile['creation_data']['ability_scores']}")
            #             # Directly send the prompt for the next step
            #             reply = f"Scores assigned. The raw potential takes shape. Now, every adventurer has a past. Choose a Background that reflects yours. (e.g., Acolyte, Criminal, Noble, Sage...)"
            #             # reply = llm_response
            #             profile["creation_status"] = "awaiting_background_choice"
            #             save_player_profiles()
            #             say(text=reply) # Send the prepared reply directly
            #             return # End processing for this state
            #     else:
            #         # If no valid parse or approval, just send LLM reply (which might contain a suggestion or ask for clarification)
            #         say(llm_response)
            #         return
            #
            #
            # elif status == "awaiting_background_choice":
            #     # List of valid D&D 5e backgrounds (simplified)
            #     valid_backgrounds = [
            #         "Acolyte", "Charlatan", "Criminal", "Entertainer", "Folk Hero", "Guild Artisan",
            #         "Hermit", "Noble", "Outlander", "Sage", "Sailor", "Soldier", "Urchin"
            #     ]
            #
            #     # Parse player message for background
            #     background_name = None
            #     for bg in valid_backgrounds:
            #         if bg.lower() in message_text.lower():
            #             background_name = bg
            #             break
            #
            #     # Call LLM to get suggestion or clarification
            #     llm_response = handle_message(message_text, user_id)
            #
            #     # Parse LLM reply for background suggestion
            #     background_suggestion = None
            #     for bg in valid_backgrounds:
            #         if bg.lower() in llm_response.lower():
            #             background_suggestion = bg
            #             break
            #
            #     # Accept player message if valid
            #     if background_name:
            #         profile["creation_data"]["background"] = background_name
            #         logger.info(f"Stored background from player message for {user_id}: {background_name}")
            #     # Else accept LLM suggestion if valid
            #     elif background_suggestion:
            #         profile["creation_data"]["background"] = background_suggestion
            #         logger.info(f"Stored background from LLM suggestion for {user_id}: {background_suggestion}")
            #     else:
            #         # Neither found, re-prompt
            #         say(llm_response)
            #         return
            #
            #     # Save profile BEFORE applying racial bonuses and instantiation
            #     save_player_profiles()
            #
            #     # --- Apply Racial Bonuses ---
            #     creation_data = profile["creation_data"]
            #     original_scores = creation_data.get("ability_scores")
            #     species_lower = creation_data.get("species", "").lower()
            #
            #     if not original_scores:
            #         raise ValueError("Ability scores not found in creation_data.")
            #     if not species_lower:
            #         raise ValueError("Species not found in creation_data.")
            #
            #     adjusted_scores = original_scores.copy() # Start with assigned scores
            #     racial_info = RACIAL_BONUSES.get(species_lower, {})
            #     racial_bonuses = racial_info.get("bonuses", {})
            #     racial_traits_for_pdf = {} # Store extra info for PDF
            #
            #     logger.info(f"Applying racial bonuses for {species_lower}: {racial_bonuses}")
            #     for stat, bonus in racial_bonuses.items():
            #         if stat in adjusted_scores:
            #             adjusted_scores[stat] += bonus
            #             # Optional: Add check for score cap (e.g., max 20) if desired
            #             # if adjusted_scores[stat] > 20: adjusted_scores[stat] = 20
            #         else:
            #             logger.warning(f"Stat '{stat}' from racial bonus not found in assigned scores.")
            #
            #     # Store other racial info for PDF filling
            #     if "languages" in racial_info:
            #         racial_traits_for_pdf["languages"] = racial_info["languages"]
            #     if "speed" in racial_info:
            #         racial_traits_for_pdf["speed"] = racial_info["speed"]
            #         # Overwrite default speed if race specifies one
            #         creation_data["speed"] = racial_info["speed"]
            #     if "other_traits" in racial_info:
            #         racial_traits_for_pdf["other_traits"] = racial_info["other_traits"]
            #     # Store this back in the profile for the PDF function
            #     profile["creation_data"]["racial_traits_info"] = racial_traits_for_pdf
            #     logger.info(f"Adjusted scores for {user_id}: {adjusted_scores}")
            #     logger.info(f"Stored racial traits for PDF: {racial_traits_for_pdf}")
            #     save_player_profiles() # Save updated profile with adjusted scores/traits info
            #
            #     # --- Class Constructor Lookup ---
            #     class_key = creation_data.get("class_key")
            #     if not class_key:
            #         raise ValueError("No class_key found in creation_data.")
            #
            #     # Map class keys (lowercase) to their constructor functions
            #     CONSTRUCTOR_MAP = {
            #         "barbarian": Barbarian, "bard": Bard, "cleric": Cleric, "druid": Druid,
            #         "fighter": Fighter, "monk": Monk, "paladin": Paladin, "ranger": Ranger,
            #         "rogue": Rogue, "sorcerer": Sorcerer, "warlock": Warlock, "wizard": Wizard
            #     }
            #
            #     # Get the correct constructor function from the map
            #     # Ensure the key used for lookup is lowercased
            #     logger.info(f"Looking up constructor for class_key: '{class_key}' (lowercase: '{class_key.lower()}')") # ADDED LOGGING
            #     CharacterConstructor = CONSTRUCTOR_MAP.get(class_key.lower())
            #     if CharacterConstructor is None:
            #         # This should ideally not happen if class_key was validated earlier, but good practice
            #         raise ValueError(f"Invalid or unsupported class key '{class_key}' for constructor lookup.")
            #
            #     # Prepare arguments for instantiation
            #     persona_name = None
            #     if "persona" in creation_data and isinstance(creation_data["persona"], dict):
            #         persona_name = creation_data["persona"].get("name")
            #     init_args = {
            #         "name": persona_name or f"Adventurer_{user_id[-4:]}",
            #         "species": creation_data.get("species"),
            #         "background": creation_data.get("background"),
            #         "speed": creation_data.get("speed"), # Use potentially race-adjusted speed
            #         **adjusted_scores # Use the racially adjusted scores
            #     }
            #     # Filter out None values (like speed if not adjusted)
            #     init_args = {k: v for k, v in init_args.items() if v is not None}
            #
            #     logger.info(f"Instantiating character for {user_id} with args: {init_args}")
            #
            #     # Call the retrieved constructor function
            #     new_character = CharacterConstructor(**init_args)
            #
            #     CHARACTER_SESSIONS[user_id] = new_character
            #     character = new_character
            #
            #     profile["creation_status"] = "awaiting_backstory_input"
            #     save_player_profiles()
            #     response_text = handle_message(message_text, user_id)
            #     say(text=response_text)
            #     return
            # --- END COMMENTED OUT STEPS ---

            elif status == "awaiting_backstory_input":
                 # Character object should exist from persona selection step
                    ability_scores_list = selected_persona.get("ability_scores", [15, 14, 13, 12, 10, 8]) # Default standard array
                    background_name = selected_persona.get("background", "Folk Hero") # Default background

                    # Assign scores from list (Placeholder: default order STR, DEX, CON, INT, WIS, CHA)
                    # TODO: Update preset_personas.json with pre-assigned scores for better results.
                    default_assignment_order = ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"]
                    if len(ability_scores_list) == 6:
                        assigned_scores = dict(zip(default_assignment_order, sorted(ability_scores_list, reverse=True)))
                        logger.warning(f"Used default score assignment for {user_id} based on list: {assigned_scores}. Update JSON for specific assignments.")
                    else:
                        logger.error(f"Invalid ability_scores list length in persona '{selected_persona['name']}'. Falling back to default scores.")
                        assigned_scores = {"strength": 15, "dexterity": 14, "constitution": 13, "intelligence": 12, "wisdom": 10, "charisma": 8}

                    # Store initial data
                    profile["creation_data"]["class_key"] = class_key
                    profile["creation_data"]["background"] = background_name
                    profile["creation_data"]["ability_scores"] = assigned_scores # Store pre-bonus scores
                    profile["creation_data"]["species"] = "Human" # Always Human

                    # Apply Racial Bonuses (Human: +1 all)
                    adjusted_scores = {stat: score + 1 for stat, score in assigned_scores.items()}
                    racial_info = RACIAL_BONUSES.get("human", {})
                    racial_traits_for_pdf = {}
                    if "languages" in racial_info: racial_traits_for_pdf["languages"] = racial_info["languages"]
                    # Humans don't have a specific speed override or other traits in RACIAL_BONUSES dict
                    profile["creation_data"]["racial_traits_info"] = racial_traits_for_pdf
                    logger.info(f"Applied Human racial bonus for {user_id}. Adjusted scores: {adjusted_scores}")

                    # --- Class Constructor Lookup ---
                    CONSTRUCTOR_MAP = {
                        "barbarian": Barbarian, "bard": Bard, "cleric": Cleric, "druid": Druid,
                        "fighter": Fighter, "monk": Monk, "paladin": Paladin, "ranger": Ranger,
                        "rogue": Rogue, "sorcerer": Sorcerer, "warlock": Warlock, "wizard": Wizard
                    }
                    CharacterConstructor = CONSTRUCTOR_MAP.get(class_key)
                    if CharacterConstructor is None:
                        logger.error(f"Invalid class key '{class_key}' from persona '{selected_persona['name']}'. Defaulting to Fighter.")
                        CharacterConstructor = Fighter
                        profile["creation_data"]["class_key"] = "fighter" # Correct profile data

                    # Prepare arguments for instantiation
                    init_args = {
                        "name": selected_persona.get("name"),
                        "species": "Human",
                        "background": background_name,
                        **adjusted_scores # Use the racially adjusted scores
                    }
                    init_args = {k: v for k, v in init_args.items() if v is not None}

                    logger.info(f"Instantiating character for {user_id} with args: {init_args}")
                    try:
                        new_character = CharacterConstructor(**init_args)
                        CHARACTER_SESSIONS[user_id] = new_character
                        character = new_character # Make available for PDF generation

                        # --- Generate and Upload PDF Immediately ---
                        blank_sheet_rel_path = "Blank Sheets/D&D 5e FormFillable Calculating Charsheet1.7 StatBig Multiclass.pdf"
                        blank_sheet_abs_path = os.path.normpath(os.path.join(script_dir, "..", blank_sheet_rel_path))
                        temp_dir = tempfile.mkdtemp()
                        char_name_safe = re.sub(r'[\\/*?:"<>|]', "", getattr(character, 'name', 'character'))
                        output_filename = f"{char_name_safe}_{user_id}_L1.pdf"
                        output_pdf_path = os.path.join(temp_dir, output_filename)

                        logger.info(f"Attempting to fill PDF: {blank_sheet_abs_path} -> {output_pdf_path}")
                        fill_success = fill_character_sheet(character, profile["creation_data"], blank_sheet_abs_path, output_pdf_path, logger)

                        pdf_comment = f"Excellent choice: {selected_persona['name']} – {selected_persona['archetype']}.\n\nYour Level 1 character sheet is attached, based on the chosen persona. Now, tell me about their origins, motivations, flaws... their story."

                        if fill_success:
                            try:
                                logger.info(f"Uploading filled PDF for user {user_id}")
                                response = client.files_upload_v2(
                                    channel=event["channel"],
                                    file=output_pdf_path,
                                    title=f"{getattr(character, 'name', 'Character')} - Level 1 Sheet",
                                    initial_comment=pdf_comment,
                                )
                                if response.get("ok"):
                                    logger.info(f"Successfully uploaded PDF for user {user_id}")
                                else:
                                    logger.error(f"Slack API error uploading file for {user_id}: {response.get('error', 'Unknown error')}")
                                    say("I generated your character sheet, but couldn't seem to send it. Apologies. Please tell me about your character's backstory.") # Send text fallback
                            except SlackApiError as e:
                                logger.error(f"Slack API error uploading file for {user_id}: {e.response['error']}", exc_info=True)
                                say("I generated your character sheet, but encountered an error sending it. My apologies. Please tell me about your character's backstory.")
                            except Exception as e:
                                logger.error(f"Unexpected error uploading file for {user_id}: {e}", exc_info=True)
                                say("Something went wrong sending your character sheet. Please tell me about your character's backstory.")
                            finally:
                                try:
                                    shutil.rmtree(temp_dir)
                                except Exception as e: logger.error(f"Failed to clean up temp dir {temp_dir}: {e}")
                        else:
                            say("I couldn't generate the character sheet PDF, apologies. Let's proceed anyway. Please tell me about your character's backstory.")

                        # --- Update Status and Save ---
                        profile["creation_status"] = "awaiting_backstory_input"
                        save_player_profiles()
                        return # End processing for this state

                    except Exception as e:
                        logger.error(f"Error instantiating character or generating PDF for {user_id} from persona: {e}", exc_info=True)
                        say("Apologies, I encountered an issue setting up your character from that persona. Let's try selecting again.")
                        profile["creation_status"] = "awaiting_persona_selection" # Revert state
                        if user_id in CHARACTER_SESSIONS: del CHARACTER_SESSIONS[user_id] # Clean up partial session
                        save_player_profiles()
                        return
                    # --- END NEW LOGIC (triggered by direct user message match) ---

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
                        # Persona found in LLM response, proceed with instantiation
                        profile["creation_data"]["persona"] = llm_selected_persona
                        llm_selected_persona["claimed"] = True
                        save_preset_personas()

                        # --- DUPLICATED LOGIC: Instantiate directly from persona ---
                        logger.info(f"Persona '{llm_selected_persona['name']}' selected via LLM interpretation for {user_id}. Instantiating character.")
                        class_key = llm_selected_persona.get("class", "fighter").lower()
                        ability_scores_list = llm_selected_persona.get("ability_scores", [15, 14, 13, 12, 10, 8])
                        background_name = llm_selected_persona.get("background", "Folk Hero")
                        default_assignment_order = ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"]
                        if len(ability_scores_list) == 6:
                            assigned_scores = dict(zip(default_assignment_order, sorted(ability_scores_list, reverse=True)))
                            logger.warning(f"Used default score assignment for {user_id} based on list: {assigned_scores}. Update JSON for specific assignments.")
                        else:
                            logger.error(f"Invalid ability_scores list length in persona '{llm_selected_persona['name']}'. Falling back.")
                            assigned_scores = {"strength": 15, "dexterity": 14, "constitution": 13, "intelligence": 12, "wisdom": 10, "charisma": 8}

                        profile["creation_data"]["class_key"] = class_key
                        profile["creation_data"]["background"] = background_name
                        profile["creation_data"]["ability_scores"] = assigned_scores
                        profile["creation_data"]["species"] = "Human"

                        adjusted_scores = {stat: score + 1 for stat, score in assigned_scores.items()}
                        racial_info = RACIAL_BONUSES.get("human", {})
                        racial_traits_for_pdf = {}
                        if "languages" in racial_info: racial_traits_for_pdf["languages"] = racial_info["languages"]
                        profile["creation_data"]["racial_traits_info"] = racial_traits_for_pdf
                        logger.info(f"Applied Human racial bonus for {user_id}. Adjusted scores: {adjusted_scores}")

                        CONSTRUCTOR_MAP = {
                            "barbarian": Barbarian, "bard": Bard, "cleric": Cleric, "druid": Druid,
                            "fighter": Fighter, "monk": Monk, "paladin": Paladin, "ranger": Ranger,
                            "rogue": Rogue, "sorcerer": Sorcerer, "warlock": Warlock, "wizard": Wizard
                        }
                        CharacterConstructor = CONSTRUCTOR_MAP.get(class_key)
                        if CharacterConstructor is None:
                            logger.error(f"Invalid class key '{class_key}' from persona '{llm_selected_persona['name']}'. Defaulting to Fighter.")
                            CharacterConstructor = Fighter
                            profile["creation_data"]["class_key"] = "fighter"

                        init_args = {
                            "name": llm_selected_persona.get("name"), "species": "Human", "background": background_name, **adjusted_scores
                        }
                        init_args = {k: v for k, v in init_args.items() if v is not None}

                        logger.info(f"Instantiating character for {user_id} with args: {init_args}")
                        try:
                            new_character = CharacterConstructor(**init_args)
                            CHARACTER_SESSIONS[user_id] = new_character
                            character = new_character

                            blank_sheet_rel_path = "Blank Sheets/D&D 5e FormFillable Calculating Charsheet1.7 StatBig Multiclass.pdf"
                            blank_sheet_abs_path = os.path.normpath(os.path.join(script_dir, "..", blank_sheet_rel_path))
                            temp_dir = tempfile.mkdtemp()
                            char_name_safe = re.sub(r'[\\/*?:"<>|]', "", getattr(character, 'name', 'character'))
                            output_filename = f"{char_name_safe}_{user_id}_L1.pdf"
                            output_pdf_path = os.path.join(temp_dir, output_filename)

                            logger.info(f"Attempting to fill PDF: {blank_sheet_abs_path} -> {output_pdf_path}")
                            fill_success = fill_character_sheet(character, profile["creation_data"], blank_sheet_abs_path, output_pdf_path, logger)

                            # Use the LLM's original response text which likely contains the confirmation AND the prompt for backstory
                            pdf_comment = response_text + "\n\n_(Your Level 1 character sheet is attached.)_"

                            if fill_success:
                                try:
                                    logger.info(f"Uploading filled PDF for user {user_id}")
                                    upload_response = client.files_upload_v2(
                                        channel=event["channel"], file=output_pdf_path, title=f"{getattr(character, 'name', 'Character')} - Level 1 Sheet", initial_comment=pdf_comment,
                                    )
                                    if upload_response.get("ok"): logger.info(f"Successfully uploaded PDF for user {user_id}")
                                    else:
                                        logger.error(f"Slack API error uploading file for {user_id}: {upload_response.get('error', 'Unknown error')}")
                                        say(response_text + "\n\n_(I generated your character sheet, but couldn't seem to send it. Apologies.)_") # Send text fallback
                                except SlackApiError as e:
                                    logger.error(f"Slack API error uploading file for {user_id}: {e.response['error']}", exc_info=True)
                                    say(response_text + "\n\n_(I generated your character sheet, but encountered an error sending it. My apologies.)_")
                                except Exception as e:
                                    logger.error(f"Unexpected error uploading file for {user_id}: {e}", exc_info=True)
                                    say(response_text + "\n\n_(Something went wrong sending your character sheet.)_")
                                finally:
                                    try: shutil.rmtree(temp_dir)
                                    except Exception as e: logger.error(f"Failed to clean up temp dir {temp_dir}: {e}")
                            else:
                                say(response_text + "\n\n_(I couldn't generate the character sheet PDF, apologies. Let's proceed anyway.)_")

                            profile["creation_status"] = "awaiting_backstory_input"
                            save_player_profiles()
                            return

                        except Exception as e:
                            logger.error(f"Error instantiating character or generating PDF for {user_id} from LLM-interpreted persona: {e}", exc_info=True)
                            say("Apologies, I encountered an issue setting up your character from that persona. Let's try selecting again.")
                            profile["creation_status"] = "awaiting_persona_selection"
                            if user_id in CHARACTER_SESSIONS: del CHARACTER_SESSIONS[user_id]
                            save_player_profiles()
                            return
                        # --- END DUPLICATED LOGIC ---
                    else:
                        # No persona detected in LLM response either, just send LLM reply
                        say(response_text if response_text else "Could you clarify which persona you'd like?")
                        return

            # --- COMMENTED OUT ORIGINAL CREATION STEPS ---
            # elif status == "awaiting_concept":
            #     # Characters are always Human. Attempt to parse Class only.
            #     # race_match = re.search(r"\b(human|elf|dwarf|halfling|dragonborn|gnome|half-elf|half-orc|tiefling)\b", message_text_lower) # Commented out race search
            #     found_class_key = None
            #     for class_key_iter in CLASSES.keys():
            #         # Use word boundaries to avoid partial matches (e.g., 'fighter' in 'firefighter')
            #         if re.search(rf"\b{class_key_iter}\b", message_text_lower, re.IGNORECASE):
            #             found_class_key = class_key_iter
            #             break
            #
            #     # Initialize creation_data if missing
            #     if "creation_data" not in profile or not isinstance(profile["creation_data"], dict):
            #         profile["creation_data"] = {}
            #
            #     # # Update race if found - No longer needed, race is always Human
            #     # if race_match:
            #     #     race_name = race_match.group(1).capitalize()
            #     #     profile["creation_data"]["species"] = race_name
            #
            #     # Update class if found and set species to Human
            #     if found_class_key:
            #         profile["creation_data"]["class_key"] = found_class_key
            #         profile["creation_data"]["species"] = "Human" # Automatically set species
            #         logger.info(f"Stored concept for {user_id}: Race=Human, ClassKey={found_class_key}")
            #
            #         # Proceed to ability score method selection
            #         class_name_display = found_class_key.capitalize()
            #         reply = f"A Human {class_name_display}... versatile, indeed. Now, for the essence – the ability scores. Shall we use the Standard Array (15, 14, 13, 12, 10, 8) or tempt fate with rolled scores (4d6 drop lowest)?"
            #         profile["creation_status"] = "awaiting_ability_method"
            #         save_player_profiles()
            #         response_text = handle_message(message_text, user_id) # Let LLM respond with the prompt
            #         say(text=response_text)
            #         return
            #     else:
            #         # Class not found, prompt only for class
            #         save_player_profiles() # Save any potential partial data (though unlikely here)
            #         prompt = "Please provide your character's Class (e.g., Fighter, Wizard, Rogue, etc.). Remember, all adventurers aboard the PATH Variable begin as Human."
            #         say(prompt)
            #         return
            #
            # elif status == "awaiting_ability_method":
            #      if "standard" in message_text_lower or "array" in message_text_lower:
            #          scores = [15, 14, 13, 12, 10, 8]
            #          profile["creation_data"]["ability_method"] = "standard"
            #          profile["creation_data"]["ability_scores_to_assign"] = scores
            #          scores_str = ", ".join(map(str, scores))
            #          reply = f"The Standard Array it is: {scores_str}. Predictable, yet reliable. Now, assign these numbers to Strength, Dexterity, Constitution, Intelligence, Wisdom, and Charisma. How will you distribute this potential? (e.g., 'STR 15, DEX 14, CON 13, INT 10, WIS 12, CHA 8')"
            #          profile["creation_status"] = "awaiting_ability_assignment"
            #          save_player_profiles() # Save state change
            #          response_text = handle_message(message_text, user_id)
            #          say(text=response_text)
            #          return
            #      elif "roll" in message_text_lower or "4d6" in message_text_lower:
            #          # Simulate rolling 4d6 drop lowest
            #          import random
            #          rolls = []
            #          for _ in range(6):
            #              dice = sorted([random.randint(1, 6) for _ in range(4)], reverse=True)
            #              rolls.append(sum(dice[:3]))
            #          rolled_scores = sorted(rolls, reverse=True)
            #          logger.info(f"Rolled scores for {user_id}: {rolled_scores}")
            #
            #          profile["creation_data"]["ability_method"] = "rolled"
            #          profile["creation_data"]["ability_scores_to_assign"] = rolled_scores
            #          scores_str = ", ".join(map(str, rolled_scores))
            #          profile["creation_status"] = "awaiting_ability_assignment"
            #          save_player_profiles() # Save state change
            #          response_text = handle_message(message_text, user_id)
            #          say(text=response_text)
            #          return
            #      else:
            #          response_text = handle_message(message_text, user_id)
            #          say(text=response_text)
            #          return
            #
            # elif status == "awaiting_ability_assignment":
            #     # Parse player's message for explicit assignment
            #     assignments = {}
            #     pattern = re.compile(
            #         r"^\s*(?:\d+\.\s*)?"  # Optional leading number (e.g., "1. ")
            #         r"(?:[-_]{1,2})?"  # Optional opening markdown (e.g., "**")
            #         r"(?:(?:Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma)\s+\()?"  # Optional full name (e.g., "Strength (")
            #         r"\b(STR|DEX|CON|INT|WIS|CHA)\b"  # Capture the abbreviation (STR, DEX, etc.)
            #         r"(?:\))?"  # Optional closing parenthesis
            #         # r"(?:[*_]{1,2})?"  # Optional closing markdown for name/abbr
            #         r"\s*[:=]{1,2}\s*"  # Separator (:, ::, =) surrounded by optional space
            #         r"(?:[*_]{1,2})?"  # Optional markdown around score
            #         r"(\d+)"  # Capture the score
            #         r".*",  # Match rest of the line (description, etc.)
            #     )
            #     matches = pattern.findall(message_text)
            #     valid_parse = True
            #     assigned_scores_list = []
            #
            #     if len(matches) == 6:
            #         for stat, score_str in matches:
            #             try:
            #                 score = int(score_str)
            #                 if 3 <= score <= 20:
            #                     assignments[stat.upper()] = score
            #                     assigned_scores_list.append(score)
            #                 else:
            #                     valid_parse = False
            #                     reply = f"Score {score} for {stat.upper()} seems unusual. Scores typically range from 3 to 20."
            #                     break
            #             except ValueError:
            #                 valid_parse = False
            #                 reply = f"Couldn't understand the score '{score_str}' for {stat.upper()}."
            #                 break
            #     else:
            #         valid_parse = False
            #
            #     # If explicit assignment parse failed, check if user approved LLM suggestion
            #     approval_phrases = ["yes", "yep", "sure", "sounds good", "looks good", "okay", "ok", "alright", "fine", "do it", "go ahead"]
            #     approved = any(phrase in message_text.lower() for phrase in approval_phrases)
            #     suggestion_available = "suggested_ability_scores" in profile.get("creation_data", {})
            #
            #     if (not valid_parse or len(matches) != 6) and approved and suggestion_available:
            #         # Use the saved suggestion
            #         assignments = profile["creation_data"]["suggested_ability_scores"]
            #         assigned_scores_list = list(assignments.values())
            #         valid_parse = True
            #         reply = None  # Clear any previous reply
            #     # Call LLM to get suggestion or clarification
            #     llm_response = handle_message(message_text, user_id)
            #
            #     # Parse LLM reply for suggested assignment - Improved Regex
            #     # Handles formats like: "1. **Strength (STR):** 15 - Description" or "CHA: 8"
            #     suggestion_pattern = re.compile(
            #         r"^\s*(?:\d+\.\s*)?"  # Optional leading number (e.g., "1. ")
            #         r"(?:[-_]{1,2})?"  # Optional opening markdown (e.g., "**")
            #         r"(?:(?:Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma)\s+\()?"  # Optional full name (e.g., "Strength (")
            #         r"\b(STR|DEX|CON|INT|WIS|CHA)\b"  # Capture the abbreviation (STR, DEX, etc.)
            #         r"(?:\))?"  # Optional closing parenthesis
            #         # r"(?:[*_]{1,2})?"  # Optional closing markdown for name/abbr
            #         r"\s*[:=]{1,2}\s*"  # Separator (:, ::, =) surrounded by optional space
            #         r"(?:[*_]{1,2})?"  # Optional markdown around score
            #         r"(\d+)"  # Capture the score
            #         r".*",  # Match rest of the line (description, etc.)
            #         re.IGNORECASE | re.MULTILINE
            #     )
            #     suggestion_matches = suggestion_pattern.findall(llm_response)
            #     suggested_scores = {}
            #     # Check if we found 6 unique stats
            #     if len(set(match[0].upper() for match in suggestion_matches)) == 6:
            #         try:
            #             for stat, score_str in suggestion_matches:
            #                 score = int(score_str)
            #                 suggested_scores[stat.upper()] = score
            #             # Save suggestion in profile
            #             profile.setdefault("creation_data", {})["suggested_ability_scores"] = suggested_scores
            #             save_player_profiles()
            #         except Exception:
            #             pass  # Ignore parse errors, don't save suggestion
            #
            #     if valid_parse and len(assignments) == 6:
            #         # Validate against stored scores
            #         scores_to_assign = sorted(profile["creation_data"].get("ability_scores_to_assign", []), reverse=True)
            #         if sorted(assigned_scores_list, reverse=True) != scores_to_assign:
            #             scores_str = ", ".join(map(str, scores_to_assign))
            #             reply = f"That assignment doesn't seem right. Ensure you use all six scores ({scores_str}) exactly once. Use the format: STR 15, DEX 14, CON 13, INT 10, WIS 12, CHA 8"
            #         else:
            #             # Store assignments in profile
            #             profile["creation_data"]["ability_scores"] = {
            #                 "strength": assignments.get("STR", 0),
            #                 "dexterity": assignments.get("DEX", 0),
            #                 "constitution": assignments.get("CON", 0),
            #                 "intelligence": assignments.get("INT", 0),
            #                 "wisdom": assignments.get("WIS", 0),
            #                 "charisma": assignments.get("CHA", 0),
            #             }
            #             logger.info(f"Stored ability scores for {user_id}: {profile['creation_data']['ability_scores']}")
            #             # Directly send the prompt for the next step
            #             reply = f"Scores assigned. The raw potential takes shape. Now, every adventurer has a past. Choose a Background that reflects yours. (e.g., Acolyte, Criminal, Noble, Sage...)"
            #             # reply = llm_response
            #             profile["creation_status"] = "awaiting_background_choice"
            #             save_player_profiles()
            #             say(text=reply) # Send the prepared reply directly
            #             return # End processing for this state
            #     else:
            #         # If no valid parse or approval, just send LLM reply (which might contain a suggestion or ask for clarification)
            #         say(llm_response)
            #         return
            #
            #
            # elif status == "awaiting_background_choice":
            #     # List of valid D&D 5e backgrounds (simplified)
            #     valid_backgrounds = [
            #         "Acolyte", "Charlatan", "Criminal", "Entertainer", "Folk Hero", "Guild Artisan",
            #         "Hermit", "Noble", "Outlander", "Sage", "Sailor", "Soldier", "Urchin"
            #     ]
            #
            #     # Parse player message for background
            #     background_name = None
            #     for bg in valid_backgrounds:
            #         if bg.lower() in message_text.lower():
            #             background_name = bg
            #             break
            #
            #     # Call LLM to get suggestion or clarification
            #     llm_response = handle_message(message_text, user_id)
            #
            #     # Parse LLM reply for background suggestion
            #     background_suggestion = None
            #     for bg in valid_backgrounds:
            #         if bg.lower() in llm_response.lower():
            #             background_suggestion = bg
            #             break
            #
            #     # Accept player message if valid
            #     if background_name:
            #         profile["creation_data"]["background"] = background_name
            #         logger.info(f"Stored background from player message for {user_id}: {background_name}")
            #     # Else accept LLM suggestion if valid
            #     elif background_suggestion:
            #         profile["creation_data"]["background"] = background_suggestion
            #         logger.info(f"Stored background from LLM suggestion for {user_id}: {background_suggestion}")
            #     else:
            #         # Neither found, re-prompt
            #         say(llm_response)
            #         return
            #
            #     # Save profile BEFORE applying racial bonuses and instantiation
            #     save_player_profiles()
            #
            #     # --- Apply Racial Bonuses ---
            #     creation_data = profile["creation_data"]
            #     original_scores = creation_data.get("ability_scores")
            #     species_lower = creation_data.get("species", "").lower()
            #
            #     if not original_scores:
            #         raise ValueError("Ability scores not found in creation_data.")
            #     if not species_lower:
            #         raise ValueError("Species not found in creation_data.")
            #
            #     adjusted_scores = original_scores.copy() # Start with assigned scores
            #     racial_info = RACIAL_BONUSES.get(species_lower, {})
            #     racial_bonuses = racial_info.get("bonuses", {})
            #     racial_traits_for_pdf = {} # Store extra info for PDF
            #
            #     logger.info(f"Applying racial bonuses for {species_lower}: {racial_bonuses}")
            #     for stat, bonus in racial_bonuses.items():
            #         if stat in adjusted_scores:
            #             adjusted_scores[stat] += bonus
            #             # Optional: Add check for score cap (e.g., max 20) if desired
            #             # if adjusted_scores[stat] > 20: adjusted_scores[stat] = 20
            #         else:
            #             logger.warning(f"Stat '{stat}' from racial bonus not found in assigned scores.")
            #
            #     # Store other racial info for PDF filling
            #     if "languages" in racial_info:
            #         racial_traits_for_pdf["languages"] = racial_info["languages"]
            #     if "speed" in racial_info:
            #         racial_traits_for_pdf["speed"] = racial_info["speed"]
            #         # Overwrite default speed if race specifies one
            #         creation_data["speed"] = racial_info["speed"]
            #     if "other_traits" in racial_info:
            #         racial_traits_for_pdf["other_traits"] = racial_info["other_traits"]
            #     # Store this back in the profile for the PDF function
            #     profile["creation_data"]["racial_traits_info"] = racial_traits_for_pdf
            #     logger.info(f"Adjusted scores for {user_id}: {adjusted_scores}")
            #     logger.info(f"Stored racial traits for PDF: {racial_traits_for_pdf}")
            #     save_player_profiles() # Save updated profile with adjusted scores/traits info
            #
            #     # --- Class Constructor Lookup ---
            #     class_key = creation_data.get("class_key")
            #     if not class_key:
            #         raise ValueError("No class_key found in creation_data.")
            #
            #     # Map class keys (lowercase) to their constructor functions
            #     CONSTRUCTOR_MAP = {
            #         "barbarian": Barbarian, "bard": Bard, "cleric": Cleric, "druid": Druid,
            #         "fighter": Fighter, "monk": Monk, "paladin": Paladin, "ranger": Ranger,
            #         "rogue": Rogue, "sorcerer": Sorcerer, "warlock": Warlock, "wizard": Wizard
            #     }
            #
            #     # Get the correct constructor function from the map
            #     # Ensure the key used for lookup is lowercased
            #     logger.info(f"Looking up constructor for class_key: '{class_key}' (lowercase: '{class_key.lower()}')") # ADDED LOGGING
            #     CharacterConstructor = CONSTRUCTOR_MAP.get(class_key.lower())
            #     if CharacterConstructor is None:
            #         # This should ideally not happen if class_key was validated earlier, but good practice
            #         raise ValueError(f"Invalid or unsupported class key '{class_key}' for constructor lookup.")
            #
            #     # Prepare arguments for instantiation
            #     persona_name = None
            #     if "persona" in creation_data and isinstance(creation_data["persona"], dict):
            #         persona_name = creation_data["persona"].get("name")
            #     init_args = {
            #         "name": persona_name or f"Adventurer_{user_id[-4:]}",
            #         "species": creation_data.get("species"),
            #         "background": creation_data.get("background"),
            #         "speed": creation_data.get("speed"), # Use potentially race-adjusted speed
            #         **adjusted_scores # Use the racially adjusted scores
            #     }
            #     # Filter out None values (like speed if not adjusted)
            #     init_args = {k: v for k, v in init_args.items() if v is not None}
            #
            #     logger.info(f"Instantiating character for {user_id} with args: {init_args}")
            #
            #     # Call the retrieved constructor function
            #     new_character = CharacterConstructor(**init_args)
            #
            #     CHARACTER_SESSIONS[user_id] = new_character
            #     character = new_character
            #
            #     profile["creation_status"] = "awaiting_backstory_input"
            #     save_player_profiles()
            #     response_text = handle_message(message_text, user_id)
            #     say(text=response_text)
            #     return
            # --- END COMMENTED OUT STEPS ---

            elif status == "awaiting_backstory_input":
                 # Character object should exist from persona selection step
                 # PDF was already generated and sent
                 backstory_notes = message_text
                 profile["backstory_notes"] = backstory_notes
                 logger.info(f"Stored backstory notes for {user_id}")

                 # --- PDF Generation Logic Removed (moved to persona selection) ---
                 # if not character:
                 #     logger.error(f"User {user_id} in state '{status}' but no character object found!")
                 #     say("I seem to have misplaced your character details. Could you remind me of your Race and Class?")
                 #     profile["creation_status"] = "awaiting_concept" # Reset state
                 #     save_player_profiles()
                 #     return
                 #
                 # # Use the generic fillable sheet for now
                 # blank_sheet_rel_path = "Blank Sheets/D&D 5e FormFillable Calculating Charsheet1.7 StatBig Multiclass.pdf"
                 # # Construct absolute path relative to the script directory
                 # blank_sheet_abs_path = os.path.normpath(os.path.join(script_dir, "..", blank_sheet_rel_path))
                 #
                 # # Create a temporary directory for the output PDF
                 # temp_dir = tempfile.mkdtemp()
                 # char_name_safe = re.sub(r'[\\/*?:"<>|]', "", getattr(character, 'name', 'character')) # Sanitize name
                 # output_filename = f"{char_name_safe}_{user_id}_L1.pdf"
                 # output_pdf_path = os.path.join(temp_dir, output_filename)
                 #
                 # logger.info(f"Attempting to fill PDF: {blank_sheet_abs_path} -> {output_pdf_path}")
                 # # Pass profile data to PDF filler for racial traits access
                 # fill_success = fill_character_sheet(character, profile["creation_data"], blank_sheet_abs_path, output_pdf_path, logger)
                 #
                 # if fill_success:
                 #     try:
                 #         logger.info(f"Uploading filled PDF for user {user_id}")
                 #         response = client.files_upload_v2(
                 #             channel=event["channel"], # Upload to the DM channel
                 #             file=output_pdf_path, # Correct parameter name is 'file'
                 #             title=f"{getattr(character, 'name', 'Character')} - Level 1 Sheet",
                 #             initial_comment="Intriguing details... noted.\n\nHere is your Level 1 character sheet, fresh off the arcane presses. The journey from here to Level 10 is now yours to navigate. Choose wisely... or amusingly.",
                 #         )
                 #         if response.get("ok"):
                 #             logger.info(f"Successfully uploaded PDF for user {user_id}")
                 #             profile["creation_status"] = "character_created" # Final state
                 #             # Optionally clear the character session if no longer needed
                 #             # if user_id in CHARACTER_SESSIONS:
                 #             #     del CHARACTER_SESSIONS[user_id]
                 #             #     logger.info(f"Cleared character session for {user_id}")
                 #             save_player_profiles()
                 #         else:
                 #             logger.error(f"Slack API error uploading file for {user_id}: {response.get('error', 'Unknown error')}")
                 #             say("I generated your character sheet, but couldn't seem to send it through the tubes. Apologies.")
                 #
                 #     except SlackApiError as e:
                 #         logger.error(f"Slack API error uploading file for {user_id}: {e.response['error']}", exc_info=True)
                 #         say("I generated your character sheet, but encountered an unexpected wire crossing when trying to send it. My apologies.")
                 #     except Exception as e:
                 #         logger.error(f"Unexpected error uploading file for {user_id}: {e}", exc_info=True)
                 #         say("Something went wrong while trying to send your character sheet.")
                 #     finally:
                 #         # Clean up the temporary directory and file
                 #         try:
                 #             shutil.rmtree(temp_dir)
                 #             logger.info(f"Cleaned up temporary directory: {temp_dir}")
                 #         except Exception as e:
                 #             logger.error(f"Failed to clean up temporary directory {temp_dir}: {e}")
                 # else:
                 #     # Filling failed, already logged in helper function
                 #     say("I seem to have smudged the ink while preparing your character sheet. Could you perhaps try telling me about your backstory again?")
                 #     # Keep status as awaiting_backstory_input to allow retry
                 #     try:
                 #         shutil.rmtree(temp_dir) # Still try to clean up
                 #     except Exception as e:
                 #         logger.error(f"Failed to clean up temporary directory {temp_dir} after PDF fill failure: {e}")

                 # Transition to final state after getting backstory
                 profile["creation_status"] = "character_created"
                 save_player_profiles()
                 # Let the LLM provide a concluding remark for this phase
                 response_text = handle_message(message_text, user_id)
                 say(text=response_text)
                 return # End processing for this state

            # --- Commented out Leveling Logic ---
            # elif status.startswith("leveling_L"):
            #     if not character: # Should not happen if status is leveling_L*, but check anyway
            #          logger.error(f"User {user_id} in leveling state '{status}' but no character object found!")
            #          # reply = "Something's gone wrong, I've lost track of your character. We may need to restart." # Keep for logging?
            #          profile["creation_status"] = "awaiting_concept" # Reset
            #          save_player_profiles() # Save reset state
            #          # Let handle_message explain the error
            #          response_text = handle_message("Error: Lost character object during leveling.", user_id)
            #          say(text=response_text)
            #          return
            #     else:
            #         current_level = getattr(character, 'level', 0) # Get current level from object
            #         if f"leveling_L{current_level}" != status:
            #              logger.warning(f"Mismatch between profile status '{status}' and character level {current_level}. Trusting character object.")
            #              status = f"leveling_L{current_level}"
            #              profile["creation_status"] = status
            #
            #         if "level up" in message_text_lower:
            #             if current_level < 10:
            #                 next_level = current_level + 1
            #                 # Removed inner try block
            #                 # Level up by adding required XP
            #                 xp_needed = experience_at_level(next_level) - character.experience
            #                 if xp_needed > 0:
            #                     character.experience += xp_needed
            #                     logger.info(f"Leveled up {user_id} to {character.level} by adding {xp_needed} XP.")
            #
            #                     # Check if level actually updated
            #                     if character.level != next_level:
            #                          logger.warning(f"Level did not update correctly for {user_id}. Expected {next_level}, got {character.level}. Forcing.")
            #                          character.level = next_level # Force update if XP didn't trigger it
            #
            #                     # Get details after level up
            #                     max_hp = getattr(character, 'max_hp', 'N/A')
            #                     new_features = []
            #                     if hasattr(character, '_class_levels'):
            #                         for level_data in character._class_levels:
            #                              if level_data.get('level') == next_level:
            #                                  new_features = [f['name'] for f in level_data.get('features', [])]
            #                                  break
            #                     features_str = ", ".join(new_features) if new_features else "None this level"
            #                     level_up_details = f"Max HP is now {max_hp}. New Features: {features_str}."
            #
            #                     # Check for ASI based on level data
            #                     needs_asi = False
            #                     asi_increase_this_level = 0
            #                     if hasattr(character, '_class_levels'):
            #                         for level_data in character._class_levels:
            #                              if level_data.get('level') == next_level:
            #                                  asi_increase_this_level = level_data.get('ability_score_bonuses', 0)
            #                                  break
            #                     # Check if the *total* bonus increased compared to the previous level's total
            #                     previous_asi_bonus = profile.get('last_total_asi_bonus', 0)
            #                     if asi_increase_this_level > previous_asi_bonus:
            #                         needs_asi = True
            #                         profile['last_total_asi_bonus'] = asi_increase_this_level # Store new total
            #
            #                     reply = f"Level {next_level} achieved! {level_up_details}"
            #                     if needs_asi:
            #                         reply += "\nYou also gain an Ability Score Improvement. Choose two scores to increase by 1, or one score to increase by 2 (e.g., 'STR+1 CON+1' or 'DEX+2'). Feats are not currently supported."
            #                         profile["creation_status"] = f"awaiting_asi_L{next_level}"
            #                         save_player_profiles() # Save state change
            #                         # Let handle_message generate the response (including ASI prompt)
            #                         response_text = handle_message(message_text, user_id)
            #                         say(text=response_text)
            #                         return
            #                     # TODO: Check for other choices (spells, subclass features) - This might need more states or LLM handling
            #                     else:
            #                         # No choices this level, prompt for next
            #                         if next_level < 10:
            #                             # reply += f"\nSay 'level up' when ready for Level {next_level + 1}." # Keep for logging?
            #                             profile["creation_status"] = f"leveling_L{next_level}"
            #                             save_player_profiles() # Save state change
            #                             # Let handle_message generate the response (including next level prompt)
            #                             response_text = handle_message(message_text, user_id)
            #                             say(text=response_text)
            #                             return
            #                         else: # Reached Level 10
            #                             # reply += "\nYou have reached Level 10! See you down the line..." # Keep for logging?
            #                             profile["creation_status"] = "creation_complete" # Original state before change
            #                             save_player_profiles() # Save final state
            #                             # Let handle_message generate the final response
            #                             response_text = handle_message(message_text, user_id)
            #                             say(text=response_text)
            #                             # Export JSON (Keep this direct feedback)
            #                             try:
            #                                 final_data = dict(character)
            #                                 final_json = json.dumps(final_data, indent=2, default=str)
            #                                 filename = f"character_{user_id}_L10.json"
            #                                 filepath = os.path.join(data_dir, filename)
            #                                 with open(filepath, "w", encoding="utf-8") as f: f.write(final_json)
            #                                 logger.info(f"Saved final character sheet for {user_id} to {filepath}")
            #                                 say(f"Your Level 10 character data has been saved ({filename}).")
            #                             except Exception as e:
            #                                 logger.error(f"Failed to export/save character JSON for {user_id}: {e}")
            #                                 say("Your Level 10 character data is ready, but I encountered an issue saving the file.")
            #                             # Clean up session
            #                             if user_id in CHARACTER_SESSIONS: del CHARACTER_SESSIONS[user_id]
            #                 # Removed inner except block - errors will be caught by the main one
            #
            #             else: # Already level 10 <<< Dedenting this else block by one level
            #                 # reply = "You've already reached the pinnacle of this particular journey (Level 10)." # Keep for logging?
            #                 # Let handle_message respond
            #                 response_text = handle_message(message_text, user_id)
            #                 say(text=response_text)
            #                 return
            #         else: # Message wasn't 'level up' - This part is already correct
            #             # Handle other messages during leveling (e.g., asking for advice)
            #             response_text = handle_message(message_text, user_id) # Pass to general handler
            #             say(text=response_text)
            #             return
            #
            # elif status.startswith("awaiting_asi_L"):
            #      if not character: # Check character exists
            #          logger.error(f"User {user_id} in ASI state '{status}' but no character object found!")
            #          # reply = "Something's gone wrong, I've lost track of your character. We may need to restart." # Keep for logging?
            #          profile["creation_status"] = "awaiting_concept" # Reset
            #          save_player_profiles() # Save reset state
            #          # Let handle_message explain the error
            #          response_text = handle_message("Error: Lost character object during ASI.", user_id)
            #          say(text=response_text)
            #          return
            #      else:
            #          level_choice = int(status.split("L")[-1])
            #          choice_applied = False
            #          reply = None # Reset reply for this block
            #          # Removed inner try block
            #          # Parse ASI: "STR+1 CON+1" or "DEX+2"
            #          asi_double_match = re.match(r"([A-Z]{3})\s*\+1\s*([A-Z]{3})\s*\+1", message_text, re.IGNORECASE)
            #          asi_single_match = re.match(r"([A-Z]{3})\s*\+2", message_text, re.IGNORECASE)
            #
            #          stats_to_update = {}
            #          valid_stats = ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"]
            #
            #          if asi_double_match:
            #                 stat1_abbr = asi_double_match.group(1).upper()
            #                 stat2_abbr = asi_double_match.group(2).upper()
            #                 stat1 = stat1_abbr.lower()
            #                 stat2 = stat2_abbr.lower()
            #
            #                 if stat1 == stat2:
            #                     reply = "You must choose two *different* scores to increase by 1."
            #                 elif stat1 not in valid_stats or stat2 not in valid_stats:
            #                     reply = "Invalid ability score specified. Use STR, DEX, CON, INT, WIS, or CHA."
            #                 else:
            #                     stats_to_update = {stat1: 1, stat2: 1}
            #                     reply = f"ASI (+1 {stat1_abbr}, +1 {stat2_abbr}) applied for Level {level_choice}."
            #                     choice_applied = True
            #          elif asi_single_match:
            #                 stat_abbr = asi_single_match.group(1).upper()
            #                 stat = stat_abbr.lower()
            #                 if stat not in valid_stats:
            #                     reply = "Invalid ability score specified. Use STR, DEX, CON, INT, WIS, or CHA."
            #                 else:
            #                     stats_to_update = {stat: 2}
            #                     reply = f"ASI (+2 {stat_abbr}) applied for Level {level_choice}."
            #                     choice_applied = True
            #          else:
            #                 reply = "Invalid choice format. Use 'STAT+1 STAT+1' or 'STAT+2'."
            #
            #          if choice_applied:
            #                 # Manually update the base stats on the character object
            #                 for stat, increase in stats_to_update.items():
            #                     current_value = getattr(character, stat, 0)
            #                     new_value = current_value + increase
            #                     # Add check for score cap (usually 20)
            #                     if new_value > 20:
            #                         logger.warning(f"ASI for {user_id} would exceed 20 for {stat}. Capping at 20.")
            #                         new_value = 20
            #                         increase = 20 - current_value # Adjust increase if capped
            #                         if increase < 0 : increase = 0 # Avoid negative increase if already 20+
            #
            #                     if increase > 0: # Only apply if there's an actual increase
            #                         setattr(character, stat, new_value)
            #                         logger.info(f"Updated {stat} from {current_value} to {new_value} for {user_id}")
            #                     else:
            #                         logger.info(f"{stat} already at cap for {user_id}. No increase applied.")
            #
            #                 # Recalculate dependent stats manually if needed (HP, modifiers)
            #                 # The library's setters might handle this, but character.py source suggests maybe not.
            #                 # Let's assume we need to update HP at least.
            #                 if hasattr(character, 'constitution'):
            #                     # Re-calculate max_hp based on potentially new CON
            #                     # Need the get_maximum_hp logic or similar
            #                     # Simplified: Add con_mod change * level to max_hp? Risky.
            #                     # Best practice would be a recalculate method on the object, which isn't obvious.
            #                     # For now, log a warning to manually check HP/modifiers.
            #                     logger.warning(f"ASI applied for {user_id}. Manual check/recalculation of HP and modifiers might be needed.")
            #                     # If CON changed, update current HP relative to max HP change?
            #                     # character.current_hp = character.max_hp # Full heal on ASI? Or just add change?
            #
            #                 if level_choice < 10:
            #                     # reply += f"\nSay 'level up' when ready for Level {level_choice + 1}." # Keep for logging?
            #                     profile["creation_status"] = f"leveling_L{level_choice}" # Back to leveling state for this level
            #                     save_player_profiles() # Save state change
            #                     # Let handle_message generate the response (including next level prompt)
            #                     response_text = handle_message(message_text, user_id)
            #                     say(text=response_text)
            #                     return
            #                 else: # Reached Level 10 after ASI
            #                     profile["creation_status"] = "creation_complete" # Original state before change
            #                     save_player_profiles() # Save final state
            #                     response_text = handle_message(message_text, user_id)
            #                     say(text=response_text)
            #                     try:
            #                         final_data = dict(character)
            #                         final_json = json.dumps(final_data, indent=2, default=str)
            #                         filename = f"character_{user_id}_L10.json"
            #                         filepath = os.path.join(data_dir, filename)
            #                         with open(filepath, "w", encoding="utf-8") as f: f.write(final_json)
            #                         logger.info(f"Saved final character sheet for {user_id} to {filepath}")
            #                         say(f"Your Level 10 character data has been saved ({filename}).")
            #                     except Exception as e:
            #                         logger.error(f"Failed to export/save character JSON for {user_id}: {e}")
            #                         say("Your Level 10 character data is ready, but I encountered an issue saving the file.")
            #                     if user_id in CHARACTER_SESSIONS: del CHARACTER_SESSIONS[user_id]
            #
            #          if reply:
            #              response_text = handle_message(message_text, user_id)
            #              say(text=response_text)
            #              return
            # --- End Commented out Leveling Logic ---

            elif status == "character_created": # Changed from creation_complete
                 # Character creation is done, pass to the general message handler
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
                      # reply = "We seem to be lost in the script. Let's start again from the beginning. What Race and Class calls to you?" # Keep for logging?
                      profile["creation_status"] = "awaiting_concept"
                      profile["creation_data"] = {} # Clear partial data
                      save_player_profiles() # Save reset state
                      # Let handle_message explain
                      response_text = handle_message("Error: Unexpected state without character. Restarting creation.", user_id)
                      say(text=response_text)
                      return # <<< ADDING THIS RETURN BACK

                 # Removed the redundant `if character: return` line that was here

        except Exception as e: # Line 758 (Indent Level 2)
             logger.error(f"Error during character creation state machine (State: {status}) for user {user_id}: {e}", exc_info=True)
             # reply = f"Apologies, a snag in the threads of fate... ({e}). Let's try that again. Where were we?" # Keep for logging?
             # Consider resetting state or asking user to clarify last step
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
