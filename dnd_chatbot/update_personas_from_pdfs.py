import os
import json
import logging
from pdf_character_parser import parse_character_pdf, BaseCharacter

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Define paths relative to this script's location
script_dir = os.path.dirname(os.path.abspath(__file__))
char_sheets_dir = os.path.abspath(os.path.join(script_dir, '..', 'CharSheets'))
personas_json_path = os.path.join(script_dir, 'data', 'preset_personas.json')

def update_personas_from_pdfs():
    """
    Reads character data from PDFs in CharSheets/ and updates
    preset_personas.json with class, ability scores, and background.
    """
    if not os.path.isdir(char_sheets_dir):
        logger.error(f"Character sheets directory not found: {char_sheets_dir}")
        return

    if not os.path.exists(personas_json_path):
        logger.error(f"Preset personas JSON file not found: {personas_json_path}")
        return

    # Load existing personas data
    try:
        with open(personas_json_path, 'r', encoding='utf-8') as f:
            personas_data = json.load(f)
        logger.info(f"Loaded {len(personas_data)} personas from {personas_json_path}")
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON from {personas_json_path}: {e}")
        return
    except Exception as e:
        logger.error(f"Error reading {personas_json_path}: {e}")
        return

    updated_count = 0
    pdf_files = [f for f in os.listdir(char_sheets_dir) if f.lower().endswith('.pdf') and f.lower() != 'blank.pdf']

    logger.info(f"Found {len(pdf_files)} PDF files in {char_sheets_dir} (excluding Blank.pdf).")

    for pdf_filename in pdf_files:
        pdf_path = os.path.join(char_sheets_dir, pdf_filename)
        logger.info(f"--- Processing {pdf_filename} ---")

        parsed_character: BaseCharacter | None = parse_character_pdf(pdf_path)

        if not parsed_character or not parsed_character.name or parsed_character.name == "Unknown Character" or parsed_character.name == "Unknown Parse Error":
            logger.warning(f"Skipping {pdf_filename}: Failed to parse character or missing name.")
            continue

        # Find matching persona in the loaded JSON data
        persona_found = False
        for persona in personas_data:
            if persona.get("name") == parsed_character.name:
                logger.info(f"Found matching persona for '{parsed_character.name}'. Updating...")
                persona_found = True

                # Update specific fields
                persona["class"] = getattr(parsed_character, 'class_name', persona.get("class", "Fighter")) # Keep old if parse fails
                persona["background"] = getattr(parsed_character, 'background', persona.get("background", "Folk Hero")) # Keep old if parse fails
                persona["ability_scores"] = [
                    getattr(parsed_character, 'strength', 10),
                    getattr(parsed_character, 'dexterity', 10),
                    getattr(parsed_character, 'constitution', 10),
                    getattr(parsed_character, 'intelligence', 10),
                    getattr(parsed_character, 'wisdom', 10),
                    getattr(parsed_character, 'charisma', 10),
                ]
                logger.debug(f"Updated persona data: Class={persona['class']}, BG={persona['background']}, Scores={persona['ability_scores']}")
                updated_count += 1
                break # Move to the next PDF once match is found

        if not persona_found:
            logger.warning(f"No matching persona found in JSON for character name '{parsed_character.name}' parsed from {pdf_filename}.")

    # Save the updated personas data back to the JSON file
    if updated_count > 0:
        try:
            with open(personas_json_path, 'w', encoding='utf-8') as f:
                json.dump(personas_data, f, indent=2)
            logger.info(f"Successfully updated {updated_count} personas in {personas_json_path}.")
        except Exception as e:
            logger.error(f"Error writing updated data to {personas_json_path}: {e}")
    else:
        logger.info("No personas were updated in the JSON file.")


if __name__ == "__main__":
    update_personas_from_pdfs()
