#!./myvenv/bin/python3

import os
import requests
import telebot
from urllib.parse import urlparse
import json
from dotenv import load_dotenv

# Load configuration from .env file
load_dotenv()

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
INSTAGRAM_API_URL = 'https://instagram.embedez.com/'
UGUU_API_URL = 'https://uguu.se/upload'

# Initialize the bot
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

def get_reel_info(reel_url):
    headers = {
        'Content-Type': 'text/plain;charset=UTF-8',
        'Accept': 'text/x-component',
        'Sec-Fetch-Site': 'same-origin',
        'Accept-Language': 'en-GB,en;q=0.9',
        'Sec-Fetch-Mode': 'cors',
        'Host': 'instagram.embedez.com',
        'Origin': 'https://instagram.embedez.com',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Safari/605.1.15 Ddg/18.5',
        'Referer': 'https://instagram.embedez.com/',
        'Sec-Fetch-Dest': 'empty',
        'Connection': 'keep-alive',
        'Priority': 'u=3, i',
        'Next-Action': 'ab9bd115bb96cb5d8aab870a379d201c07d3b005',
    }

    payload = [{"url": reel_url}]

    try:
        response = requests.post(INSTAGRAM_API_URL, headers=headers, json=payload)
        response.raise_for_status()

        # Parse the response to get reel information
        response_text = response.content.decode('utf-8')
        parts = response_text.split('\n')
        if len(parts) >= 2:
            data_part = parts[1]
            if data_part.startswith('1:'):
                json_data = json.loads(data_part[2:])
                if json_data.get('success', False):
                    # Extract user and content information
                    user_display_name = json_data.get('data', {}).get('user', {}).get('displayName', '')
                    content_title = json_data.get('data', {}).get('content', {}).get('title', '')

                    # Choose text for the link (title or displayName)
                    link_text = content_title if content_title else user_display_name

                    # Get the video URL
                    media = json_data.get('data', {}).get('content', {}).get('media', [])
                    if media and len(media) > 0:
                        video_url = media[0].get('source', {}).get('url')
                        if video_url:
                            return {
                                'video_url': video_url,
                                'link_text': link_text,
                                'filename': os.path.basename(urlparse(reel_url).path) + '.mp4'
                            }
        return None
    except Exception as e:
        return str(e)

def download_video(video_url, filename):
    try:
        video_response = requests.get(video_url, stream=True)
        video_response.raise_for_status()

        with open(filename, 'wb') as f:
            for chunk in video_response.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        return str(e)

def upload_to_uguu(filename):
    try:
        with open(filename, 'rb') as f:
            response = requests.post(UGUU_API_URL, files={'files[]': f})
            response.raise_for_status()

            json_response = response.json()
            if json_response.get('success', False) and json_response.get('files'):
                if len(json_response['files']) > 0:
                    return json_response['files'][0].get('url', '')
            return ''
    except Exception as e:
        return str(e)

@bot.message_handler(regexp=r'https?://(www\.)?instagram\.com/reel/')
def handle_reel(message):
    try:
        reel_url = message.text
        chat_id = message.chat.id

        # Remove typing notification
        bot.send_chat_action(chat_id, 'upload_video')

        # Get information about the reel
        reel_info = get_reel_info(reel_url)
        if not reel_info or isinstance(reel_info, str):
            bot.set_message_reaction(chat_id, message.id, reaction=[telebot.types.ReactionTypeEmoji("💔")])
            return

        # Download the video
        download_result = download_video(reel_info['video_url'], reel_info['filename'])
        if isinstance(download_result, str) or not download_result:
            error_msg = f"Failed to download video: {download_result if isinstance(download_result, str) else 'unknown error'}"
            bot.reply_to(message, error_msg, disable_notification=True)
            return

        # Upload to uguu.se
        direct_link = upload_to_uguu(reel_info['filename'])

        # Remove temporary file
        if os.path.exists(reel_info['filename']):
            os.remove(reel_info['filename'])

        if direct_link.startswith('http'):
            # Format text with hyperlink
            link_text = reel_info['link_text'] or 'Watch video'
            reply_text = f'<a href="{direct_link}">{link_text}</a>'
            bot.reply_to(message, reply_text, parse_mode='HTML', disable_notification=True)
        else:
            bot.reply_to(message, f"Error uploading to uguu.se: {direct_link}", disable_notification=True)
    except Exception as e:
        bot.reply_to(message, f"An error occurred: {str(e)}", disable_notification=True)

if __name__ == '__main__':
    if not TELEGRAM_BOT_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN not found in .env file")
        exit(1)

    print("Bot running...")
    bot.infinity_polling()

