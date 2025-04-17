import os
import json
import logging
import argparse
import re # For sanitizing filenames

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Global Paths ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PLAYER_PROFILES_PATH = os.path.join(SCRIPT_DIR, 'data', 'player_profiles.json')

# --- Helper Functions ---
def load_player_profiles(file_path):
    """Loads the entire player profiles dictionary."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            profiles = json.load(f)
        logger.info(f"Successfully loaded player profiles from {file_path}")
        return profiles
    except FileNotFoundError:
        logger.error(f"Player profiles file not found: {file_path}")
        return {}
    except json.JSONDecodeError:
        logger.error(f"Error decoding JSON from player profiles file: {file_path}")
        return {}
    except Exception as e:
        logger.error(f"An unexpected error occurred loading player profiles from {file_path}: {e}")
        return {}

def sanitize_filename(name):
    """Removes or replaces characters invalid for filenames."""
    # Remove leading/trailing whitespace
    name = name.strip()
    # Replace spaces with underscores
    name = name.replace(' ', '_')
    # Remove characters that are not alphanumeric, underscore, or hyphen
    name = re.sub(r'[^\w\-]+', '', name)
    # Ensure the filename is not empty after sanitization
    return name if name else "Unnamed_Persona"


def parse_conversation_logs(logs_dir: str, output_dir: str, player_profiles: dict):
    """
    Parses JSON conversation logs and writes the message text to separate TXT files,
    named after the corresponding persona.

    Args:
        logs_dir: The directory containing the JSON log files (e.g., USERID_conversation.json).
        output_dir: The directory where the parsed readable TXT files will be saved.
        player_profiles: A dictionary containing player profile data keyed by user ID.
    """
    logger.info(f"Starting log parsing from directory: {logs_dir}")
    if not os.path.isdir(logs_dir):
        logger.error(f"Logs input directory not found: {logs_dir}")
        return
    if not player_profiles:
        logger.warning("Player profiles data is empty. Cannot map user IDs to persona names.")
        # Continue parsing but use UserID for filenames as fallback? Or exit?
        # For now, let's proceed using UserID as fallback filename.

    # Create the output directory if it doesn't exist
    try:
        os.makedirs(output_dir, exist_ok=True)
        logger.info(f"Ensured readable logs output directory exists: {output_dir}")
    except OSError as e:
        logger.error(f"Failed to create readable logs output directory {output_dir}: {e}")
        return

    parsed_count = 0
    error_count = 0

    # Iterate through files in the logs directory
    for json_filename in os.listdir(logs_dir):
        if json_filename.endswith(".json") and "_conversation.json" in json_filename:
            input_filepath = os.path.join(logs_dir, json_filename)
            logger.debug(f"Processing log file: {input_filepath}")

            # Extract User ID from filename
            user_id_match = re.match(r"^(U[A-Z0-9]+)_conversation\.json$", json_filename)
            if not user_id_match:
                logger.warning(f"Could not extract User ID from filename: {json_filename}. Skipping.")
                error_count += 1
                continue
            user_id = user_id_match.group(1)

            # Get Persona Name from profiles
            persona_name = "Unknown_Persona" # Default
            if user_id in player_profiles:
                profile = player_profiles[user_id]
                # Navigate potentially nested structure for persona name
                persona_data = profile.get("creation_data", {}).get("persona", {})
                name_from_profile = persona_data.get("name") if isinstance(persona_data, dict) else None

                if name_from_profile:
                    persona_name = sanitize_filename(name_from_profile)
                else:
                    logger.warning(f"Persona name not found in profile for user {user_id}. Using UserID as fallback.")
                    persona_name = user_id # Fallback to UserID if name missing
            else:
                logger.warning(f"User ID {user_id} not found in player profiles. Using UserID as fallback filename.")
                persona_name = user_id # Fallback to UserID if profile missing

            # Construct output path using persona name
            output_filename = f"{persona_name}_readable_log.txt"
            output_filepath = os.path.join(output_dir, output_filename)

            try:
                with open(input_filepath, 'r', encoding='utf-8') as infile, \
                     open(output_filepath, 'w', encoding='utf-8') as outfile:

                    try:
                        log_data = json.load(infile)
                        if not isinstance(log_data, list):
                            logger.warning(f"Skipping {json_filename}: Expected a list of messages, found {type(log_data)}.")
                            error_count += 1
                            continue

                        message_count = 0
                        for message in log_data:
                            if isinstance(message, dict) and "text" in message:
                                text_content = message.get("text", "").strip()
                                if text_content: # Only write if there's actual text
                                    # Optionally add sender info if available
                                    sender = message.get("user", "Unknown") # Get user ID
                                    # You might want to map sender ID to a name here if possible/needed
                                    outfile.write(f"[{sender}]: {text_content}\n\n")
                                    message_count += 1
                            # else: # Log less verbosely or skip logging non-text messages
                                # logger.debug(f"Skipping message in {json_filename}: Missing 'text' key, not a dictionary, or empty text.")

                        if message_count > 0:
                            parsed_count += 1
                            logger.debug(f"Successfully parsed {message_count} messages and wrote: {output_filepath}")
                        else:
                            logger.info(f"No text messages found in {json_filename}. Output file {output_filepath} might be empty.")
                            # Optionally delete empty output files?
                            # os.remove(output_filepath)

                    except json.JSONDecodeError as e:
                        logger.error(f"Error decoding JSON from {json_filename}: {e}")
                        error_count += 1
                    except Exception as e: # Catch other potential errors during file processing
                        logger.error(f"An unexpected error occurred processing {json_filename}: {e}")
                        error_count += 1

            except IOError as e:
                logger.error(f"Error opening or writing file for {json_filename} -> {output_filename}: {e}")
                error_count += 1
            except Exception as e: # Catch other potential errors during file handling
                logger.error(f"An unexpected error occurred handling files for {json_filename}: {e}")
                error_count += 1
        else:
             logger.debug(f"Skipping non-JSON or non-conversation file: {json_filename}")


    logger.info(f"Readable log generation complete. Successfully generated: {parsed_count} files. Errors encountered for: {error_count} files.")

if __name__ == "__main__":
    # Define directories relative to the script's location
    default_logs_dir = os.path.join(SCRIPT_DIR, "data", "conversation_logs")
    default_output_dir = os.path.join(SCRIPT_DIR, "data", "readable_logs") # New output dir

    # Set up argument parser
    parser = argparse.ArgumentParser(description="Parse Slack conversation logs (JSON) into readable text files named by persona.")
    parser.add_argument(
        "--logs-dir",
        default=default_logs_dir,
        help=f"Directory containing JSON log files (default: {default_logs_dir})"
    )
    parser.add_argument(
        "--output-dir",
        default=default_output_dir,
        help=f"Directory to save readable TXT files (default: {default_output_dir})"
    )
    parser.add_argument(
        "--profiles-path",
        default=PLAYER_PROFILES_PATH,
        help=f"Path to the player profiles JSON file (default: {PLAYER_PROFILES_PATH})"
    )
    args = parser.parse_args()

    # Load player profiles
    player_profiles_data = load_player_profiles(args.profiles_path)

    # Run the parsing function
    parse_conversation_logs(args.logs_dir, args.output_dir, player_profiles_data)
