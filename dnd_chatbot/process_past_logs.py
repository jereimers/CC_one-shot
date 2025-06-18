import os
import json
import glob
import logging
from pathlib import Path

# --- Configuration ---
LOGS_DIR = Path(__file__).parent / "data" / "conversation_logs"
HISTORIES_DIR = Path(__file__).parent / "data" / "conversation_histories"
BOT_USER_ID = "U08MKD37A20" # Confirmed Bot User ID

# Configure basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def process_log_file(log_file_path: Path, target_user_id: str, histories_dir: Path):
    """Processes a single log file and converts it to the history format."""
    logger.info(f"Processing log file: {log_file_path.name} for user {target_user_id}")
    history = []
    try:
        with open(log_file_path, "r", encoding="utf-8") as f:
            log_data = json.load(f)

        if not isinstance(log_data, list):
            logger.warning(f"Skipping {log_file_path.name}: Expected a list of messages, found {type(log_data)}")
            return

        for message in log_data:
            if not isinstance(message, dict):
                logger.debug(f"Skipping non-dict item in {log_file_path.name}")
                continue

            msg_type = message.get("type")
            msg_subtype = message.get("subtype")
            msg_text = message.get("text", "").strip()
            msg_user = message.get("user")
            msg_bot_id = message.get("bot_id")

            # Filter out irrelevant messages
            if msg_type != "message" or not msg_text:
                logger.debug(f"Skipping message (type: {msg_type}, subtype: {msg_subtype}, empty text?) in {log_file_path.name}")
                continue
            # Ignore subtypes like edits, file comments, bot messages we don't want, etc.
            # Allow None subtype (regular messages)
            if msg_subtype is not None and msg_subtype not in ["bot_message", "thread_broadcast"]: # Adjust if other subtypes are needed
                 logger.debug(f"Skipping message subtype: {msg_subtype} in {log_file_path.name}")
                 continue

            role = None
            content = msg_text

            if msg_bot_id and msg_user == BOT_USER_ID:
                role = "assistant"
            elif not msg_bot_id and msg_user == target_user_id:
                role = "user"
            # Add more conditions here if needed, e.g., handling specific bot messages differently

            if role:
                history.append({"role": role, "content": content})
            else:
                 logger.debug(f"Could not determine role for message TS: {message.get('ts')} in {log_file_path.name}")


        if history:
            histories_dir.mkdir(parents=True, exist_ok=True)
            history_file_path = histories_dir / f"{target_user_id}.json"
            try:
                with open(history_file_path, "w", encoding="utf-8") as f_hist:
                    json.dump(history, f_hist, ensure_ascii=False, indent=2)
                logger.info(f"Successfully wrote history for {target_user_id} to {history_file_path.name} ({len(history)} messages)")
            except Exception as e:
                logger.error(f"Failed to write history file for {target_user_id}: {e}", exc_info=True)
        else:
            logger.info(f"No relevant messages found for {target_user_id} in {log_file_path.name}. No history file created.")

    except json.JSONDecodeError:
        logger.error(f"Error decoding JSON from {log_file_path.name}. Skipping.")
    except Exception as e:
        logger.error(f"An unexpected error occurred processing {log_file_path.name}: {e}", exc_info=True)


def main():
    """Finds and processes all conversation log files."""
    logger.info(f"Starting log processing...")
    logger.info(f"Logs directory: {LOGS_DIR}")
    logger.info(f"Histories directory: {HISTORIES_DIR}")
    logger.info(f"Bot User ID: {BOT_USER_ID}")

    log_files = glob.glob(str(LOGS_DIR / "*_conversation.json"))

    if not log_files:
        logger.warning("No conversation log files found matching '*_conversation.json'. Exiting.")
        return

    logger.info(f"Found {len(log_files)} log files to process.")

    for log_file in log_files:
        log_file_path = Path(log_file)
        # Extract user ID from filename (e.g., UXXXXXXXX_conversation.json -> UXXXXXXXX)
        target_user_id = log_file_path.name.split("_")[0]
        # Basic validation for Slack User ID format (starts with U or W)
        if target_user_id.startswith(("U", "W")) and len(target_user_id) > 1:
             process_log_file(log_file_path, target_user_id, HISTORIES_DIR)
        else:
             logger.warning(f"Skipping file with unexpected name format: {log_file_path.name}")


    logger.info("Log processing finished.")

if __name__ == "__main__":
    main()
