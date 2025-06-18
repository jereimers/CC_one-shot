import os
import argparse
import logging
from dotenv import load_dotenv
from send_dm import send_slack_dm
import sys
import json

script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(script_dir)
data_dir = os.path.join(script_dir, "data")

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load active player profiles
profiles_path = os.path.join(data_dir, "player_profiles.json")
with open(profiles_path, "r", encoding="utf-8") as f:
    PLAYER_PROFILES = json.load(f)
    


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send a direct message via Slack to entire party, with optional file attachment.")
    parser.add_argument("--text", help="The message text, or the path to a .txt file containing the message text.")
    parser.add_argument("--file", help="Optional path to a file to attach to the message.")

    args = parser.parse_args()

    message_content = ""
    # Check if --text argument is a file path
    if os.path.isfile(args.text) and args.text.lower().endswith('.txt'):
        try:
            with open(args.text, 'r', encoding='utf-8') as f:
                message_content = f.read()
            logger.info(f"Read message content from file: {args.text}")
        except IOError as e:
            logger.error(f"Error reading message text file {args.text}: {e}")
            print(f"Error: Could not read message text file '{args.text}'.")
            exit(1) # Exit if text file cannot be read
        except Exception as e:
            logger.error(f"Unexpected error reading text file {args.text}: {e}", exc_info=True)
            print(f"Error: An unexpected error occurred reading '{args.text}'.")
            exit(1)
    else:
        # Treat --text as a literal string
        message_content = args.text
        logger.info("Using literal string for message content.")

    if not message_content:
        print("Error: Message content is empty.")
        exit(1)

    # Get active party members' IDs
    PARTY_PROFILES = []
    for profile in PLAYER_PROFILES:
        details = PLAYER_PROFILES.get(profile, {})
        if details.get("creation_status") == "character_created":
            PARTY_PROFILES.append(profile)
            # logger.info(f"User ${profile} added to party.")
    
    for user in PARTY_PROFILES:
        send_slack_dm(user, message_content, args.file)
   
