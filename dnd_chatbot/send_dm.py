import os
import argparse
import logging
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

party = {"Beauregard": "U08N3LJNMLL", 
         "Jordan": "U08N3LH5BCL", 
         "Ingrid": "U08N3LJ5UVA",
         "Vaultsy": "U08N3LJAK0U",
         "Winifred": "U08N91YNS20"
         }

def send_slack_dm(user_id: str, message_text: str, attachment_path: str | None = None):
    """
    Sends a direct message to a Slack user, optionally with a file attachment.

    Args:
        user_id: The Slack User ID of the recipient.
        message_text: The text content of the message.
        attachment_path: Optional path to a file to attach.
    """
    # Load environment variables (specifically the Slack Bot Token)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    dotenv_path = os.path.join(script_dir, '.env')
    load_dotenv(dotenv_path=dotenv_path)

    slack_bot_token = os.getenv("SLACK_BOT_TOKEN")

    if not slack_bot_token:
        logger.error("SLACK_BOT_TOKEN not found in .env file.")
        return

    try:
        client = WebClient(token=slack_bot_token)
        logger.info(f"Slack client initialized. Attempting to send DM to {user_id}.")

        if attachment_path:
            # --- Get DM Channel ID for File Upload ---
            dm_channel_id = None
            try:
                logger.info(f"Opening conversation with user {user_id} to get DM channel ID...")
                response = client.conversations_open(users=user_id)
                if response.get("ok"):
                    dm_channel_id = response.get("channel", {}).get("id")
                    if dm_channel_id:
                        logger.info(f"Obtained DM channel ID: {dm_channel_id}")
                    else:
                        logger.error("conversations.open response did not contain channel ID.")
                        print("Error: Could not determine the direct message channel ID for the user.")
                        return
                else:
                    error_msg = response.get('error', 'Unknown error')
                    logger.error(f"Slack API error opening conversation: {error_msg}")
                    print(f"Error opening conversation with user: {error_msg}")
                    return
            except SlackApiError as e:
                logger.error(f"Slack API error opening conversation: {e.response['error']}", exc_info=True)
                print(f"Error opening conversation with user: {e.response['error']}")
                return
            except Exception as e:
                logger.error(f"Unexpected error opening conversation: {e}", exc_info=True)
                print(f"An unexpected error occurred while opening the conversation: {e}")
                return
            # --- End Get DM Channel ID ---

            if not os.path.exists(attachment_path):
                logger.error(f"Attachment file not found: {attachment_path}")
                print(f"Error: Attachment file not found at '{attachment_path}'")
                return

            logger.info(f"Uploading file '{attachment_path}' to channel {dm_channel_id} with message...")
            try:
                # Use files_upload_v2 with the obtained DM Channel ID
                response = client.files_upload_v2(
                    channel=dm_channel_id, # Use the specific DM channel ID
                    file=attachment_path,
                    initial_comment=message_text,
                    # title=os.path.basename(attachment_path) # Optional: set a title
                )
                if response.get("ok"):
                    logger.info(f"Successfully sent message with attachment to {user_id}.")
                    print("Message with attachment sent successfully.")
                else:
                    error_msg = response.get('error', 'Unknown error')
                    logger.error(f"Slack API error uploading file: {error_msg}")
                    print(f"Error sending message with attachment: {error_msg}")

            except SlackApiError as e:
                logger.error(f"Slack API error uploading file: {e.response['error']}", exc_info=True)
                print(f"Error sending message with attachment: {e.response['error']}")
            except Exception as e: # Catch other potential exceptions during upload
                logger.error(f"Unexpected error uploading file: {e}", exc_info=True)
                print(f"An unexpected error occurred during file upload: {e}")

        else:
            logger.info("Sending text-only message...")
            try:
                response = client.chat_postMessage(
                    channel=user_id, # Send directly to user ID for DM
                    text=message_text
                )
                if response.get("ok"):
                    logger.info(f"Successfully sent text message to {user_id}.")
                    print("Text message sent successfully.")
                else:
                    error_msg = response.get('error', 'Unknown error')
                    logger.error(f"Slack API error sending message: {error_msg}")
                    print(f"Error sending message: {error_msg}")

            except SlackApiError as e:
                logger.error(f"Slack API error sending message: {e.response['error']}", exc_info=True)
                print(f"Error sending message: {e.response['error']}")
            except Exception as e: # Catch other potential exceptions
                logger.error(f"Unexpected error sending message: {e}", exc_info=True)
                print(f"An unexpected error occurred sending the message: {e}")

    except Exception as e:
        logger.error(f"Failed to initialize Slack client or other setup error: {e}", exc_info=True)
        print(f"An error occurred during setup: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send a direct message via Slack, optionally with an attachment.")
    parser.add_argument("--user", help="The Slack User ID of the recipient (e.g., U0XXXXXXXXX).")
    parser.add_argument(
        "--text",
        required=True,
        help="The message text, or the path to a .txt file containing the message text."
    )
    parser.add_argument("--file", help="Optional path to a file to attach to the message.")
    parser.add_argument("--name", default="", help="The character's name associated with the Slack User of the recipient (Vaultsy, Ingrid, Winifred, Jordan, Beauregard)")
    args = parser.parse_args()
    user = ""

    if((args.user == "") & (args.name != "")):
        user = party.get(args.name)

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

    # Call the function to send the DM
    send_slack_dm(args.user, message_content, args.file)
