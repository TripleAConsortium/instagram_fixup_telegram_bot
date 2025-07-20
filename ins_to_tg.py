#!./myvenv/bin/python3

import os
import requests
import telebot
from urllib.parse import urlparse
import json
from dotenv import load_dotenv
from typing import List, Dict, Optional, Union

# Configuration
INSTAGRAM_API_URL = 'https://instagram.embedez.com/'
UGUU_API_URL = 'https://uguu.se/upload'
TMPFILES_API_URL = 'https://tmpfiles.org/api/v1/upload'
# Choose upload service: 'uguu' or 'tmpfiles'
UPLOAD_SERVICE = os.getenv('UPLOAD_SERVICE', 'uguu').lower()
DELETE_ORIGINAL_MESSAGE = os.getenv('DELETE_ORIGINAL_MESSAGE', 'false').lower() == 'true'

def setup(bot):
    @bot.message_handler(regexp=r'https?://(www\.)?instagram\.com/(reel|p)/')
    def handle_instagram_post(message):
        post_url = message.text
        process_instagram_post(bot, message, post_url)

def get_instagram_info(post_url: str) -> Optional[Dict]:
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

    payload = [{"url": post_url}]

    try:
        response = requests.post(INSTAGRAM_API_URL, headers=headers, json=payload)
        response.raise_for_status()

        # Parse the response to get post information
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
                    content_description = json_data.get('data', {}).get('content', {}).get('description', '')

                    # Choose text for the link (title, description, or displayName)
                    link_text = content_title if content_title else user_display_name

                    # Get all media (photos/videos)
                    media = json_data.get('data', {}).get('content', {}).get('media', [])
                    media_urls = []

                    for item in media:
                        if item.get('type') in ['photo', 'video']:
                            media_url = item.get('source', {}).get('url')
                            if media_url:
                                media_urls.append({
                                    'url': media_url,
                                    'type': item['type'],
                                    'filename': f"{os.path.basename(urlparse(post_url).path)}_{len(media_urls)}.{'mp4' if item['type'] == 'video' else 'jpg'}"
                                })

                    if media_urls:
                        return {
                            'media': media_urls,
                            'link_text': link_text,
                            'description': content_description if content_description else '.'
                        }
        return None
    except Exception as e:
        return str(e)

def download_media(media_url: str, filename: str) -> Union[bool, str]:
    try:
        response = requests.get(media_url, stream=True)
        response.raise_for_status()

        with open(filename, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        return str(e)

def upload_to_service(filename: str) -> str:
    if UPLOAD_SERVICE == 'tmpfiles':
        return upload_to_tmpfiles(filename)
    else:
        return upload_to_uguu(filename)

def upload_to_uguu(filename: str) -> str:
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

def upload_to_tmpfiles(filename: str) -> str:
    try:
        with open(filename, 'rb') as f:
            response = requests.post(TMPFILES_API_URL, files={'file': f})
            response.raise_for_status()

            json_response = response.json()
            if json_response.get('status') == 'success' and json_response.get('data'):
                return json_response['data'].get('url', '').replace('tmpfiles.org/', 'tmpfiles.org/dl/')
            return ''
    except Exception as e:
        return str(e)

def process_instagram_post(bot, message, post_url: str):
    try:
        chat_id = message.chat.id

        # Remove typing notification
        bot.send_chat_action(chat_id, 'upload_photo')

        # Get post information
        post_info = get_instagram_info(post_url)
        if not post_info or isinstance(post_info, str):
            bot.set_message_reaction(chat_id, message.id, reaction=[telebot.types.ReactionTypeEmoji("💔")])
            return

        # Download all media
        downloaded_files = []
        for media_item in post_info['media']:
            download_result = download_media(media_item['url'], media_item['filename'])
            if isinstance(download_result, str) or not download_result:
                error_msg = f"Failed to download media: {download_result if isinstance(download_result, str) else 'unknown error'}"
                bot.reply_to(message, error_msg, disable_notification=True)
                return
            downloaded_files.append(media_item)

        # Upload to selected service and collect links
        uploaded_links = []
        for media_item in downloaded_files:
            direct_link = upload_to_service(media_item['filename'])
            if not direct_link.startswith('http'):
                bot.reply_to(message, f"Upload error on {UPLOAD_SERVICE}: {direct_link}", disable_notification=True)
                return
            uploaded_links.append({
                'url': direct_link,
                'type': media_item['type']
            })

        # Delete temporary files
        for media_item in downloaded_files:
            if os.path.exists(media_item['filename']):
                os.remove(media_item['filename'])

        caption = ""
        if post_info.get('description'):
            first_line = post_info['description'].split('\n')[0]
            caption = f"{first_line}\n\n[Source]({post_url})"

        # If only one media, send as message with link
        if len(uploaded_links) == 1:
            media_item = uploaded_links[0]
            caption = caption.replace('\n\n', f"\n\n[{post_info.get('link_text')}]({media_item['url']}) | ")
            
            bot.send_message(
                chat_id=chat_id,
                text=caption,
                reply_to_message_id=message.message_id,
                disable_web_page_preview=False,
                parse_mode="Markdown"
            )
        else:
            # If multiple media, send as media group
            media_group = []
            for idx, media_item in enumerate(uploaded_links):
                if media_item['type'] == 'photo':
                    media_group.append(telebot.types.InputMediaPhoto(media_item['url']))
                else:  # video
                    media_group.append(telebot.types.InputMediaVideo(media_item['url']))

                # Add caption to the first media only
                if idx == 0 and caption:
                    media_group[0].caption = caption
                    media_group[0].parse_mode = "Markdown"

            bot.send_media_group(
                chat_id=chat_id,
                media=media_group,
                reply_to_message_id=message.message_id  # Reply to the original message
            )

        # Delete original message if configured to do so
        if DELETE_ORIGINAL_MESSAGE:
            try:
                bot.delete_message(chat_id, message.message_id)
            except Exception as e:
                print(f"Could not delete message: {e}")

    except Exception as e:
        bot.reply_to(message, f"An error occurred: {str(e)}", disable_notification=True)
