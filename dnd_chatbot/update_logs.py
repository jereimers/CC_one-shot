import os
import json
import logging
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
LOGS_DIR = os.path.join(DATA_DIR, 'conversation_logs') # Directory for logs

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Helper Functions ---
def load_player_ids(file_path):
    """Loads user IDs from the player profiles JSON file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            profiles = json.load(f)
        return list(profiles.keys())
    except FileNotFoundError:
        logger.error(f"Player profiles file not found: {file_path}")
        return []
    except json.JSONDecodeError:
        logger.error(f"Error decoding JSON from file: {file_path}")
        return []
    except Exception as e:
        logger.error(f"An unexpected error occurred loading player IDs from {file_path}: {e}")
        return []

def save_conversation_log(user_id, messages, log_path):
    """Saves the conversation messages to a JSON file."""
    try:
        # Ensure the directory exists
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, 'w', encoding='utf-8') as f:
            json.dump(messages, f, indent=4, ensure_ascii=False)
        logger.info(f"Saved conversation log for user {user_id} to {log_path}")
    except IOError as e:
        logger.error(f"Error saving conversation log {log_path}: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred while saving log {log_path}: {e}")

# --- Main Execution ---
if __name__ == "__main__":
    if not SLACK_BOT_TOKEN:
        logger.error("SLACK_BOT_TOKEN not found in environment variables. Please check your .env file.")
        exit(1)

    # Ensure logs directory exists
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
    except OSError as e:
        logger.error(f"Failed to create logs directory {LOGS_DIR}: {e}")
        exit(1)

    player_ids = load_player_ids(PLAYER_PROFILES_PATH)
    if not player_ids:
        logger.warning("No player IDs loaded. The script will run but might not process any conversations.")
        # Decide if exiting is necessary, maybe just log and continue
        # exit(1)

    logger.info(f"Loaded {len(player_ids)} player IDs.")

    try:
        client = WebClient(token=SLACK_BOT_TOKEN)
        processed_conversations = 0
        bot_user_id = None

        # Get the bot's user ID (optional but good for filtering later if needed)
        try:
            auth_test_result = client.auth_test()
            bot_user_id = auth_test_result.get("user_id")
            if bot_user_id:
                logger.info(f"Bot User ID identified as: {bot_user_id}")
            else:
                logger.warning("Could not determine bot user ID from auth.test.")
        except SlackApiError as e:
            logger.error(f"Slack API error during auth.test: {e.response['error']}. Proceeding without bot ID.")
        except Exception as e:
            logger.error(f"Unexpected error during auth.test: {e}. Proceeding without bot ID.")


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
             exit(1) # Exit if we can't list conversations
        except Exception as e:
             logger.error(f"Unexpected error listing conversations: {e}")
             exit(1)


        logging.info(f"Found {len(all_im_conversations)} total IM conversations.")

        for conversation in all_im_conversations:
            user_id = conversation.get("user")
            channel_id = conversation.get("id")

            # Process only conversations with users listed in player_profiles.json
            if user_id in player_ids:
                logger.info(f"Processing conversation with user: {user_id} (Channel ID: {channel_id})")
                processed_conversations += 1
                conversation_messages = {} # Use dict keyed by ts to store messages and avoid duplicates
                history_cursor = None
                total_messages_fetched = 0

                try:
                    while True: # Loop for history pagination
                        logger.debug(f"Fetching history for {channel_id}, cursor: {history_cursor}")
                        history_result = client.conversations_history(
                            channel=channel_id,
                            cursor=history_cursor,
                            limit=200 # Fetch in batches
                        )
                        messages_page = history_result.get("messages", [])
                        total_messages_fetched += len(messages_page)
                        logger.debug(f"Fetched {len(messages_page)} messages this page.")

                        for message in messages_page:
                            message_ts = message.get("ts")
                            if not message_ts:
                                logger.warning(f"Skipping message in {channel_id} due to missing timestamp: {message.get('text', 'No text')[:50]}...")
                                continue # Skip messages without a timestamp

                            # Store message if not already seen
                            if message_ts not in conversation_messages:
                                conversation_messages[message_ts] = message

                            # Check if this message starts a thread and fetch replies
                            if message.get("reply_count", 0) > 0 and message.get("thread_ts") == message_ts:
                                logger.debug(f"Fetching replies for thread starting at {message_ts} in {channel_id}")
                                replies_cursor = None
                                try:
                                    while True: # Pagination for replies
                                        logger.debug(f"Fetching replies page, cursor: {replies_cursor}")
                                        replies_result = client.conversations_replies(
                                            channel=channel_id,
                                            ts=message_ts,
                                            cursor=replies_cursor,
                                            limit=200
                                        )
                                        reply_messages = replies_result.get("messages", [])
                                        # The first message in replies is the parent, skip it
                                        for reply in reply_messages[1:]:
                                            reply_ts = reply.get("ts")
                                            if reply_ts and reply_ts not in conversation_messages:
                                                conversation_messages[reply_ts] = reply
                                                total_messages_fetched += 1 # Count replies too

                                        replies_metadata = replies_result.get('response_metadata')
                                        if replies_result.get("has_more") and replies_metadata and 'next_cursor' in replies_metadata:
                                            replies_cursor = replies_metadata['next_cursor']
                                            time.sleep(1) # Respect rate limits
                                        else:
                                            break # No more replies pages
                                except SlackApiError as re:
                                    # Log rate limit errors specifically if possible
                                    if re.response.status_code == 429:
                                        retry_after = int(re.response.headers.get('Retry-After', 1))
                                        logger.warning(f"Rate limited fetching replies for {message_ts}. Retrying after {retry_after} seconds.")
                                        time.sleep(retry_after)
                                        # Consider adding logic to retry the specific replies call here
                                    else:
                                        logger.error(f"Slack API error fetching replies for ts {message_ts} in channel {channel_id}: {re.response['error']}")
                                    # Potentially break inner loop on error or retry? For now, just log.
                                except Exception as re_exc:
                                     logger.error(f"Unexpected error fetching replies for ts {message_ts} in channel {channel_id}: {re_exc}")
                                     # Potentially break inner loop on error

                        # Handle history pagination
                        response_metadata = history_result.get('response_metadata')
                        if history_result.get("has_more") and response_metadata and 'next_cursor' in response_metadata:
                            history_cursor = response_metadata['next_cursor']
                            logger.info(f"Fetching next page of history for channel {channel_id}...")
                            time.sleep(1) # Respect rate limits
                        else:
                            break # No more history pages

                    # Convert collected messages dict back to a list, sorted by timestamp
                    sorted_messages = sorted(conversation_messages.values(), key=lambda m: float(m.get('ts', 0)))

                    logger.info(f"Retrieved {len(sorted_messages)} total messages (including replies) for user {user_id}.")

                    # Save the full conversation log
                    log_file_path = os.path.join(LOGS_DIR, f"{user_id}_conversation.json")
                    save_conversation_log(user_id, sorted_messages, log_file_path)

                except SlackApiError as e:
                    if e.response.status_code == 429:
                        retry_after = int(e.response.headers.get('Retry-After', 1))
                        logger.warning(f"Rate limited fetching history for {channel_id}. Retrying after {retry_after} seconds.")
                        time.sleep(retry_after)
                        # Consider adding logic to retry the history call here
                    else:
                        logger.error(f"Slack API error fetching history for channel {channel_id}: {e.response['error']}")
                except Exception as e:
                    logger.error(f"An unexpected error occurred processing channel {channel_id}: {e}")

        if processed_conversations == 0:
             logger.warning("No conversations found matching the loaded player IDs.")

        logger.info("Log update script finished.")

    except Exception as e:
        logger.error(f"An unexpected error occurred during script execution: {e}", exc_info=True)
