import os
import json
import logging
import requests
import time
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from dotenv import load_dotenv

# --- Configuration ---
# Load environment variables from .env file located in the same directory as this script
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path=dotenv_path)

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
PLAYER_PROFILES_PATH = os.path.join(os.path.dirname(__file__), 'data', 'player_profiles.json')
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
DOWNLOADS_DIR = os.path.join(DATA_DIR, 'downloads') # Directory for downloaded files

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Helper Functions ---
def load_player_ids(file_path):
    """Loads user IDs from the player profiles JSON file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            profiles = json.load(f)
        # Ensure keys are strings if they were loaded differently
        return [str(key) for key in profiles.keys()]
    except FileNotFoundError:
        logger.error(f"Player profiles file not found: {file_path}")
        return []
    except json.JSONDecodeError:
        logger.error(f"Error decoding JSON from file: {file_path}")
        return []
    except Exception as e:
        logger.error(f"An unexpected error occurred loading player IDs from {file_path}: {e}")
        return []

def download_file(url, token, download_path):
    """Downloads a file from a Slack URL using token authentication."""
    headers = {'Authorization': f'Bearer {token}'}
    try:
        response = requests.get(url, headers=headers, stream=True, timeout=60) # Added timeout
        response.raise_for_status()  # Raise an exception for bad status codes

        with open(download_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        logger.info(f"Successfully downloaded: {os.path.basename(download_path)}")
        return True
    except requests.exceptions.Timeout:
        logger.error(f"Timeout occurred while downloading file {url}")
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Error downloading file {url}: {e}")
        return False
    except IOError as e:
        logger.error(f"Error writing file to {download_path}: {e}")
        return False
    except Exception as e:
        logger.error(f"An unexpected error occurred while downloading {url} to {download_path}: {e}")
        return False

def get_unique_filepath(directory, filename):
    """Generates a unique filepath by appending (n) if the file exists."""
    if not filename: # Handle cases where filename might be None or empty
        filename = "downloaded_file"
    base, ext = os.path.splitext(filename)
    # Sanitize base name (remove potentially problematic characters)
    safe_base = "".join(c for c in base if c.isalnum() or c in (' ', '_', '-')).rstrip()
    if not safe_base: # If sanitization removed everything
        safe_base = "file"

    counter = 1
    unique_filename = f"{safe_base}{ext}"
    unique_path = os.path.join(directory, unique_filename)

    while os.path.exists(unique_path):
        unique_filename = f"{safe_base}({counter}){ext}"
        unique_path = os.path.join(directory, unique_filename)
        counter += 1
        if counter > 1000: # Safety break to prevent infinite loops
             logger.error(f"Could not find a unique filename for {filename} after 1000 attempts.")
             return None
    return unique_path

# --- Main Execution ---
if __name__ == "__main__":
    if not SLACK_BOT_TOKEN:
        logger.error("SLACK_BOT_TOKEN not found in environment variables. Please check your .env file.")
        exit(1)

    # Ensure downloads directory exists
    try:
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    except OSError as e:
        logger.error(f"Failed to create downloads directory {DOWNLOADS_DIR}: {e}")
        exit(1)

    player_ids = load_player_ids(PLAYER_PROFILES_PATH)
    if not player_ids:
        logger.warning("No player IDs loaded. The script will run but might not find relevant conversations.")
        # exit(1) # Optional: exit if no players

    logger.info(f"Loaded {len(player_ids)} player IDs.")

    try:
        client = WebClient(token=SLACK_BOT_TOKEN)
        processed_conversations = 0
        total_files_downloaded = 0

        logger.info("Fetching direct message conversations...")
        all_im_conversations = []
        cursor = None
        try:
            while True: # Pagination for conversations.list
                conv_list_result = client.conversations_list(types="im", cursor=cursor, limit=200)
                all_im_conversations.extend(conv_list_result.get("channels", []))
                cursor = conv_list_result.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
                logger.info("Fetching next page of conversations...")
                time.sleep(1) # Respect rate limits
        except SlackApiError as e:
             logger.error(f"Slack API error listing conversations: {e.response['error']}")
             exit(1)
        except Exception as e:
             logger.error(f"Unexpected error listing conversations: {e}")
             exit(1)

        logging.info(f"Found {len(all_im_conversations)} total IM conversations.")

        for conversation in all_im_conversations:
            user_id = conversation.get("user")
            channel_id = conversation.get("id")

            # Process only conversations with users listed in player_profiles.json
            if user_id in player_ids:
                logger.info(f"Checking files for user: {user_id} (Channel ID: {channel_id})")
                processed_conversations += 1
                files_in_conv_count = 0
                files_list_cursor = None

                try:
                    while True: # Pagination for files.list
                        logger.debug(f"Fetching files.list for {channel_id}, cursor: {files_list_cursor}")
                        files_list_result = client.files_list(
                            channel=channel_id,
                            show_files_hidden_by_limit=True, # Attempt to get all files
                            cursor=files_list_cursor,
                            limit=100 # Adjust limit as needed, max 1000 for files.list
                        )
                        channel_files = files_list_result.get("files", [])
                        logger.debug(f"files.list page returned {len(channel_files)} files for channel {channel_id}.")

                        for file_info in channel_files:
                            file_id = file_info.get("id")
                            file_url = file_info.get("url_private_download")
                            file_name = file_info.get("name")
                            sender_id = file_info.get("user", "unknown_sender")

                            if file_id and file_url and file_name:
                                # Generate expected path based on unique naming convention
                                unique_download_path = get_unique_filepath(DOWNLOADS_DIR, file_name)
                                if unique_download_path is None: # Handle error from get_unique_filepath
                                    continue

                                # Check if the file *already exists* using the unique name logic
                                # This avoids re-downloading if the unique name was already generated
                                if not os.path.exists(unique_download_path):
                                    logger.info(f"Attempting to download file '{os.path.basename(unique_download_path)}' (ID: {file_id}) from sender {sender_id} in channel {channel_id}...")
                                    if download_file(file_url, SLACK_BOT_TOKEN, unique_download_path):
                                        files_in_conv_count += 1
                                    else:
                                        # Optional: attempt to remove partially downloaded file if download failed
                                        if os.path.exists(unique_download_path):
                                            try:
                                                os.remove(unique_download_path)
                                                logger.info(f"Removed partially downloaded file: {unique_download_path}")
                                            except OSError as remove_err:
                                                logger.error(f"Failed to remove partial file {unique_download_path}: {remove_err}")
                                else:
                                    logger.debug(f"File '{os.path.basename(unique_download_path)}' (ID: {file_id}) already exists. Skipping download.")
                            elif not file_url or not file_name:
                                 logger.warning(f"File ID {file_id} from files.list in channel {channel_id} missing URL or Name, cannot download.")

                        # Handle files.list pagination
                        files_list_metadata = files_list_result.get('response_metadata')
                        if files_list_result.get("has_more") and files_list_metadata and 'next_cursor' in files_list_metadata and files_list_metadata['next_cursor']:
                             files_list_cursor = files_list_metadata['next_cursor']
                             logger.info(f"Fetching next page of files.list for channel {channel_id}...")
                             time.sleep(1) # Respect rate limits
                        else:
                            break # No more files.list pages

                    if files_in_conv_count > 0:
                        logger.info(f"Downloaded {files_in_conv_count} new files for conversation with user {user_id}.")
                        total_files_downloaded += files_in_conv_count
                    else:
                        logger.info(f"No new files found to download for conversation with user {user_id}.")

                except SlackApiError as fe:
                    if fe.response.status_code == 429:
                        retry_after = int(fe.response.headers.get('Retry-After', 1))
                        logger.warning(f"Rate limited fetching files.list for {channel_id}. Retrying after {retry_after} seconds.")
                        time.sleep(retry_after)
                        # Consider adding retry logic here for the files.list call
                    else:
                        logger.error(f"Slack API error fetching files.list for channel {channel_id}: {fe.response['error']}")
                except Exception as fe_exc:
                    logger.error(f"Unexpected error fetching files.list for channel {channel_id}: {fe_exc}")

        if processed_conversations == 0:
             logger.warning("No conversations found matching the loaded player IDs.")

        logger.info(f"File download script finished. Downloaded {total_files_downloaded} new files across {processed_conversations} processed conversations.")

    except Exception as e:
        logger.error(f"An unexpected error occurred during script execution: {e}", exc_info=True)
