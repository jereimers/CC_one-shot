import os
import logging
from pypdf import PdfReader
from pypdf.errors import PdfReadError
import argparse

# Ensure dnd-character library can be found (adjust path as necessary)
import sys
script_dir = os.path.dirname(os.path.abspath(__file__))
dnd_character_path = os.path.abspath(os.path.join(script_dir, '..', 'dnd-character'))
if dnd_character_path not in sys.path:
    sys.path.append(dnd_character_path)

try:
    # Import specific classes and utility functions from dnd-character
    from dnd_character.classes import (
        Barbarian, Bard, Cleric, Druid, Fighter, Monk,
        Paladin, Ranger, Rogue, Sorcerer, Warlock, Wizard
    )
    from dnd_character.character import Character as BaseCharacter # Base class for type hints
    # SKILLS constant import seems problematic, define manually below
except ImportError as e:
    logging.error(f"Failed to import dnd-character modules: {e}. Ensure dnd-character library is present at {dnd_character_path} and installed.")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Field Mapping (Based on user input) ---
# Add more mappings here as needed for equipment, spells, features etc.
FIELD_MAP = {
    # Core Info
    "name": "character name",
    "class_name": "class",
    "level": "lvl",
    "race": "race",
    "background": "background",
    # Ability Scores
    "strength": "str score",
    "dexterity": "dex ability score",
    "constitution": "Con ability score",
    "intelligence": "Int ability score",
    "wisdom": "Text18", # Mapped via user inspection
    "charisma": "Text23", # Mapped via user inspection
    # Saving Throws Proficiencies (Checkboxes)
    "saves": {
        "strength": "str save skill",
        "dexterity": "dex saving prof",
        "constitution": "con prof", # Mapped via user inspection
        "intelligence": "con saving prof", # Mapped via user inspection (Intentional PDF inconsistency)
        "wisdom": "wis saving prof",
        "charisma": "cha saving prof",
    },
    # Skills Proficiencies & Expertise (Checkboxes)
    # Define skills manually as import is problematic
    "skills": {skill: {"prof": f"{skill.lower().replace(' ', '').replace('-', '')} prof", "exp": f"{skill.lower().replace(' ', '').replace('-', '')} exp"} for skill in [
        "Acrobatics", "Animal Handling", "Arcana", "Athletics", "Deception", "History",
        "Insight", "Intimidation", "Investigation", "Medicine", "Nature", "Perception",
        "Performance", "Persuasion", "Religion", "Sleight of Hand", "Stealth", "Survival"
    ]},
    # HP
    "max_hp": "hp",
    "current_hp": "current hp",
    "temp_hp": "temp hp",
    # AC
    "ac": "AC", # Corrected mapping
    # Hit Dice
    "hd_total": "hd tot 1", # Assuming primary class HD in first slot
    "hd_used": "hd used 1", # Assuming primary class HD used in first slot
    # Appearance/Personality
    "age": "Text100",
    "height": "Text101",
    "weight": "Text102",
    "eyes": "Text105",
    "skin": "Text104",
    "hair": "Text103",
    "personality_traits": "Text108",
    "ideals": "Text109",
    "bonds": "Text110",
    "flaws": "Text111",
    "backstory": "Text113",
    # Basic Spellcasting
    "spellcasting_class": "Text199",
    "spellcasting_ability": "Text200",
    # TODO: Add mappings for equipment, features, detailed spells, attacks etc.
}

# Map class names (lowercase) to their constructors
CLASS_CONSTRUCTORS = {
    "barbarian": Barbarian, "bard": Bard, "cleric": Cleric, "druid": Druid,
    "fighter": Fighter, "monk": Monk, "paladin": Paladin, "ranger": Ranger,
    "rogue": Rogue, "sorcerer": Sorcerer, "warlock": Warlock, "wizard": Wizard
}

def get_field_value(fields, field_name, default=None):
    """Safely retrieves a field's value ('/V') from the PDF fields dictionary."""
    if not fields or field_name not in fields:
        # logger.debug(f"Field '{field_name}' not found in PDF.")
        return default
    field = fields[field_name]
    value = field.get('/V') # '/V' usually holds the value
    # Checkboxes might have '/AS' (Appearance State) instead of '/V' for their state
    if value is None and field.get('/FT') == '/Btn': # Field Type is Button/Checkbox
        value = field.get('/AS')

    # logger.debug(f"Field '{field_name}' raw value: {value}")

    if value is None:
        return default
    # pypdf often returns names like '/Yes', '/Off'. Convert common ones.
    if isinstance(value, str):
        if value == '/Off': return False # Treat '/Off' as False for checkboxes
        if value == '/Yes': return True # Treat '/Yes' as True for checkboxes
        # Handle potential PDF Name objects (e.g., /0) for some checkboxes
        if value.startswith('/'):
             # For now, treat any non-'/Off' checkbox state as True.
             # More specific handling might be needed if other states like '/0' mean something else.
             return True
        return value.strip() # Return stripped string for text fields
    return value # Return as is (e.g., numbers)

def parse_character_pdf(pdf_path: str) -> BaseCharacter | None:
    """
    Parses a D&D 5e character sheet PDF and returns a populated dnd-character object.

    Args:
        pdf_path: The path to the character sheet PDF file.

    Returns:
        A populated dnd_character Character object, or None if critical errors occur.
        Returns a partially filled object if non-critical data is missing.
    """
    try:
        reader = PdfReader(pdf_path)
        fields = reader.get_fields()
        if not fields:
            logger.warning(f"No form fields found in '{pdf_path}'. Cannot parse character.")
            # Return a very basic default character object
            return Fighter(name="Unknown Character")

        # --- Extract Core Attributes ---
        char_name = get_field_value(fields, FIELD_MAP["name"], "Unnamed Character")
        class_name_raw = get_field_value(fields, FIELD_MAP["class_name"], "Fighter")
        # Handle potential multi-classing string like "Fighter 1 / Wizard 2" - take first part for now
        class_name = "fighter" # Default
        if isinstance(class_name_raw, str):
             class_name = class_name_raw.split('/')[0].split(' ')[0].strip().lower()
        elif class_name_raw:
             logger.warning(f"Unexpected class field type: {type(class_name_raw)}. Defaulting to Fighter.")

        level_str = get_field_value(fields, FIELD_MAP["level"], "1")
        try:
            level = int(level_str) if level_str else 1
        except ValueError:
            logger.warning(f"Invalid level '{level_str}' in PDF, defaulting to 1.")
            level = 1

        race = get_field_value(fields, FIELD_MAP["race"], "Human")
        background = get_field_value(fields, FIELD_MAP["background"], "Folk Hero")

        # --- Get Class Constructor ---
        CharacterConstructor = CLASS_CONSTRUCTORS.get(class_name, Fighter)
        if class_name not in CLASS_CONSTRUCTORS:
            logger.warning(f"Unknown class '{class_name}' found in PDF. Defaulting to Fighter.")

        # --- Extract Ability Scores ---
        abilities = {}
        default_score = 10
        for ability in ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"]:
            field_name = FIELD_MAP.get(ability)
            score_str = get_field_value(fields, field_name, str(default_score))
            try:
                abilities[ability] = int(score_str) if score_str else default_score
            except ValueError:
                logger.warning(f"Invalid {ability} score '{score_str}' in PDF, defaulting to {default_score}.")
                abilities[ability] = default_score

        # --- Instantiate Character ---
        try:
            character = CharacterConstructor(
                name=char_name,
                level=level,
                species=race,
                background=background,
                **abilities # Unpack strength=X, dexterity=Y etc.
            )
        except Exception as e:
            logger.error(f"Failed to instantiate dnd-character object: {e}", exc_info=True)
            # Return a basic object even on instantiation failure
            return Fighter(name=char_name) # Use Fighter as a fallback

        # --- Set Proficiencies (Saves & Skills) ---
        # Saving Throws
        logger.debug(f"Type of character.saving_throws: {type(character.saving_throws)}") # Add logging
        logger.debug(f"Value of character.saving_throws: {character.saving_throws}") # Add logging
        for save_ability, field_name in FIELD_MAP["saves"].items():
            is_proficient = get_field_value(fields, field_name, False) # Default to False if field missing/Off
            if is_proficient: # Checkbox value was /Yes or non-/Off
                try:
                    # Ensure saving_throws is accessed like a dict
                    if isinstance(character.saving_throws, dict):
                         character.saving_throws[save_ability].proficient = True
                         # logger.debug(f"Set saving throw proficiency for {save_ability} to True.")
                    else:
                         logger.error(f"character.saving_throws is not a dict, but a {type(character.saving_throws)}. Cannot set proficiency for {save_ability}.")
                except KeyError:
                    logger.warning(f"Could not set saving throw proficiency for '{save_ability}' (KeyError).")
                except AttributeError:
                     logger.warning(f"Could not set saving throw proficiency for '{save_ability}' (AttributeError - perhaps '{save_ability}' key exists but value has no 'proficient' attr?).")

        # Skills
        for skill_name, field_names in FIELD_MAP["skills"].items():
            prof_field = field_names["prof"]
            exp_field = field_names["exp"]

            is_proficient = get_field_value(fields, prof_field, False)
            has_expertise = get_field_value(fields, exp_field, False)

            try:
                if has_expertise:
                    character.skills[skill_name].expertise = True # type: ignore
                    # logger.debug(f"Set skill expertise for {skill_name} to True.")
                elif is_proficient:
                    character.skills[skill_name].proficient = True # type: ignore
                    # logger.debug(f"Set skill proficiency for {skill_name} to True.")
            except KeyError:
                 logger.warning(f"Could not set skill proficiency/expertise for '{skill_name}'.")


        # --- Set HP ---
        max_hp_str = "0" # Initialize before try block
        try:
            max_hp_str = get_field_value(fields, FIELD_MAP["max_hp"], "0")
            # Assuming max_hp is correctly set by the constructor based on class/level/con
            # We might only need to *read* it, not set it directly unless overriding
            # character.max_hp = int(max_hp_str) if max_hp_str else character.max_hp # Keep calculated if empty
        except ValueError:
            logger.warning(f"Invalid Max HP '{max_hp_str}' in PDF.") # This line caused the unbound error if the try block failed before assignment
        try:
            # Use getattr to safely access max_hp which might be calculated by constructor
            default_current = getattr(character, 'max_hp', 1)
            current_hp_str = get_field_value(fields, FIELD_MAP["current_hp"], str(default_current))
            character.hp = int(current_hp_str) if current_hp_str else default_current # type: ignore
        except ValueError:
             logger.warning(f"Invalid Current HP '{current_hp_str}' in PDF, defaulting to Max HP.")
             character.hp = getattr(character, 'max_hp', 1) # type: ignore
        try:
            temp_hp_str = get_field_value(fields, FIELD_MAP["temp_hp"], "0")
            character.temp_hp = int(temp_hp_str) if temp_hp_str else 0 # temp_hp seems standard
        except ValueError:
             logger.warning(f"Invalid Temp HP '{temp_hp_str}' in PDF, defaulting to 0.")
             character.temp_hp = 0

        # --- Set AC ---
        try:
            ac_str = get_field_value(fields, FIELD_MAP["ac"], "10") # Default AC 10
            character.ac = int(ac_str) if ac_str else 10 # type: ignore # Use default if empty
        except ValueError:
             logger.warning(f"Invalid AC '{ac_str}' in PDF, defaulting to 10.")
             character.ac = 10 # type: ignore

        # --- Set Hit Dice ---
        # This is simplified, assumes single class and uses the first HD fields
        try:
            hd_total_str = get_field_value(fields, FIELD_MAP["hd_total"], str(level)) # Default to level
            hd_used_str = get_field_value(fields, FIELD_MAP["hd_used"], "0")
            hd_total = int(hd_total_str) if hd_total_str else level
            hd_used = int(hd_used_str) if hd_used_str else 0
            # dnd-character library manages HD internally based on level/class,
            # but we can store the *current* count if needed, though there isn't a direct field.
            # We might need to add a custom attribute or just rely on the library's calculation.
            # For now, just log the values found.
            logger.debug(f"PDF HD Total: {hd_total}, Used: {hd_used}")
            # character.hit_dice_total = hd_total # Example if we added such an attribute
            # character.hit_dice_remaining = hd_total - hd_used # Example
        except ValueError:
            logger.warning(f"Invalid Hit Dice values in PDF (Total: '{hd_total_str}', Used: '{hd_used_str}').")


        # --- Set Descriptive Fields (as custom attributes) ---
        # Pylance reports these aren't on BaseCharacter, store with _pdf_ prefix
        raw_age = get_field_value(fields, FIELD_MAP["age"])
        character._pdf_age = str(raw_age) if raw_age is not None and not isinstance(raw_age, bool) else None # type: ignore # Ensure string or None
        character._pdf_height = get_field_value(fields, FIELD_MAP["height"]) # type: ignore
        character._pdf_weight = get_field_value(fields, FIELD_MAP["weight"]) # type: ignore
        character._pdf_eyes = get_field_value(fields, FIELD_MAP["eyes"]) # type: ignore
        character._pdf_skin = get_field_value(fields, FIELD_MAP["skin"]) # type: ignore
        character._pdf_hair = get_field_value(fields, FIELD_MAP["hair"]) # type: ignore
        character._pdf_personality_traits = get_field_value(fields, FIELD_MAP["personality_traits"]) # type: ignore
        character._pdf_ideals = get_field_value(fields, FIELD_MAP["ideals"]) # type: ignore
        character._pdf_bonds = get_field_value(fields, FIELD_MAP["bonds"]) # type: ignore
        character._pdf_flaws = get_field_value(fields, FIELD_MAP["flaws"]) # type: ignore
        character._pdf_backstory = get_field_value(fields, FIELD_MAP["backstory"]) # type: ignore


        # --- Basic Spellcasting Info (as custom attributes) ---
        # Store these for potential later use, though dnd-character handles spellcasting internally
        character._pdf_spellcasting_class = get_field_value(fields, FIELD_MAP["spellcasting_class"]) # type: ignore
        character._pdf_spellcasting_ability = get_field_value(fields, FIELD_MAP["spellcasting_ability"]) # type: ignore

        logger.info(f"Successfully parsed character '{character.name}' from '{pdf_path}'.")
        return character

    except FileNotFoundError:
        logger.error(f"Error: PDF file not found at '{pdf_path}'")
        return None
    except PdfReadError as e:
        logger.error(f"Error reading PDF '{pdf_path}': {e}")
        return None # Return None on read error
    except Exception as e:
        logger.error(f"An unexpected error occurred during PDF parsing: {e}", exc_info=True)
        # Return a basic object even on unexpected errors during parsing
        try:
            # Try to get at least the name if possible before failing
            reader = PdfReader(pdf_path)
            fields = reader.get_fields()
            name = get_field_value(fields, FIELD_MAP["name"], "Unknown Parse Error") if fields else "Unknown Parse Error"
            return Fighter(name=name)
        except: # Catch all if even reading name fails
             return Fighter(name="Unknown Parse Error")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parse a D&D 5e character sheet PDF.")
    parser.add_argument("pdf_path", help="Path to the character sheet PDF file.")
    parser.add_argument("--json", action="store_true", help="Output key character details as JSON.")

    args = parser.parse_args()

    parsed_character = parse_character_pdf(args.pdf_path)

    if parsed_character:
        print(f"--- Parsed Character Details ---")
        print(f"Name: {parsed_character.name}")
        print(f"Class: {getattr(parsed_character, 'class_name', 'N/A')}") # Use getattr for safety
        print(f"Level: {getattr(parsed_character, 'level', 'N/A')}")
        print(f"Race: {getattr(parsed_character, 'species', 'N/A')}")
        print(f"Background: {getattr(parsed_character, 'background', 'N/A')}")
        # Access HP/AC via getattr as Pylance flags them
        hp = getattr(parsed_character, 'hp', 'N/A')
        max_hp = getattr(parsed_character, 'max_hp', 'N/A')
        temp_hp = getattr(parsed_character, 'temp_hp', 'N/A')
        ac = getattr(parsed_character, 'ac', 'N/A')
        print(f"HP: {hp}/{max_hp} (Temp: {temp_hp})")
        print(f"AC: {ac}")
        print(f"Abilities: STR={getattr(parsed_character, 'strength', 'N/A')}, DEX={getattr(parsed_character, 'dexterity', 'N/A')}, CON={getattr(parsed_character, 'constitution', 'N/A')}, INT={getattr(parsed_character, 'intelligence', 'N/A')}, WIS={getattr(parsed_character, 'wisdom', 'N/A')}, CHA={getattr(parsed_character, 'charisma', 'N/A')}")

        # Use getattr for skills/saves access in case they aren't populated on base class
        saving_throws_dict = getattr(parsed_character, 'saving_throws', {})
        skills_dict = getattr(parsed_character, 'skills', {})

        proficient_saves = [s for s, obj in saving_throws_dict.items() if getattr(obj, 'proficient', False)]
        print(f"Saving Throw Profs: {', '.join(proficient_saves) or 'None'}")

        proficient_skills = [s for s, obj in skills_dict.items() if getattr(obj, 'proficient', False)]
        expert_skills = [s for s, obj in skills_dict.items() if getattr(obj, 'expertise', False)]
        print(f"Skill Profs: {', '.join(proficient_skills) or 'None'}")
        print(f"Skill Expertise: {', '.join(expert_skills) or 'None'}")

        # Print custom PDF fields
        print("\n--- Additional PDF Data ---")
        print(f"Age: {getattr(parsed_character, '_pdf_age', 'N/A')}")
        print(f"Height: {getattr(parsed_character, '_pdf_height', 'N/A')}")
        print(f"Weight: {getattr(parsed_character, '_pdf_weight', 'N/A')}")
        print(f"Eyes: {getattr(parsed_character, '_pdf_eyes', 'N/A')}")
        print(f"Skin: {getattr(parsed_character, '_pdf_skin', 'N/A')}")
        print(f"Hair: {getattr(parsed_character, '_pdf_hair', 'N/A')}")
        print(f"Personality Traits: {getattr(parsed_character, '_pdf_personality_traits', 'N/A')}")
        print(f"Ideals: {getattr(parsed_character, '_pdf_ideals', 'N/A')}")
        print(f"Bonds: {getattr(parsed_character, '_pdf_bonds', 'N/A')}")
        print(f"Flaws: {getattr(parsed_character, '_pdf_flaws', 'N/A')}")
        print(f"Spellcasting Class (PDF): {getattr(parsed_character, '_pdf_spellcasting_class', 'N/A')}")
        print(f"Spellcasting Ability (PDF): {getattr(parsed_character, '_pdf_spellcasting_ability', 'N/A')}")


        if args.json:
            import json
            output_data = {
                "name": getattr(parsed_character, 'name', None),
                "class": getattr(parsed_character, 'class_name', None),
                "level": getattr(parsed_character, 'level', None),
                "race": getattr(parsed_character, 'species', None),
                "background": getattr(parsed_character, 'background', None),
                "hp": hp if hp != 'N/A' else None,
                "max_hp": max_hp if max_hp != 'N/A' else None,
                "temp_hp": temp_hp if temp_hp != 'N/A' else None,
                "ac": ac if ac != 'N/A' else None,
                "abilities": {
                    "strength": getattr(parsed_character, 'strength', None),
                    "dexterity": getattr(parsed_character, 'dexterity', None),
                    "constitution": parsed_character.constitution,
                    "intelligence": parsed_character.intelligence,
                    "wisdom": parsed_character.wisdom,
                    "charisma": parsed_character.charisma,
                },
                "saving_throw_proficiencies": proficient_saves,
                "skill_proficiencies": proficient_skills,
                "skill_expertise": expert_skills,
                # Add more fields as needed
            }
            print("\n--- JSON Output ---")
            print(json.dumps(output_data, indent=2))
    else:
        print(f"Failed to parse character from {args.pdf_path}")
