#!./myvenv/bin/python3

import os
import sys
import subprocess
import requests
import telebot
import tempfile
from dotenv import load_dotenv

# Add parent directory to path so igram_resolver submodule is importable.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

DELETE_ORIGINAL_MESSAGE = os.getenv('DELETE_ORIGINAL_MESSAGE', 'false').lower() == 'true'
USE_DACOGRAM = os.getenv('USE_DACOGRAM', 'false').lower() == 'true'
HASHTAG = "\n\n#instagram"
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB

def setup(bot):
    @bot.message_handler(regexp=r'https?://(www\.)?instagram\.com/(reel|p|share/reel)/')
    def handle_instagram_post(message):
        process_instagram_post(bot, message, message.text)

    @bot.message_handler(regexp=r'https?://(www\.)?(tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)/')
    def handle_tiktok_post(message):
        process_tiktok_post(bot, message, message.text)


def resolve_via_igram(post_url: str) -> list[str]:
    """Resolve Instagram URL to direct media URLs via igram.world."""
    try:
        from igram_resolver.igram_resolver import resolve
        return resolve(post_url)
    except Exception as e:
        print(f"igram resolver error: {e}")
        return []


def download_file(url: str, suffix: str = ".mp4") -> str | None:
    """Download a file to a temp path. Returns path or None."""
    try:
        resp = requests.get(url, stream=True, timeout=60)
        resp.raise_for_status()
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        for chunk in resp.iter_content(chunk_size=8192):
            tmp.write(chunk)
        tmp.close()
        return tmp.name
    except Exception as e:
        print(f"Download error: {e}")
        return None


def compress_video(path: str) -> str:
    """Compress video to fit under MAX_FILE_SIZE. Returns path (same or new)."""
    size = os.path.getsize(path)
    if size <= MAX_FILE_SIZE:
        return path

    # Calculate target bitrate: (target_size_bits * 0.95) / duration.
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True, text=True, timeout=30
        )
        duration = float(__import__('json').loads(probe.stdout)["format"]["duration"])
    except Exception:
        duration = 60.0

    target_bitrate = int((MAX_FILE_SIZE * 8 * 0.90) / duration)

    out_path = path + ".compressed.mp4"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", path, "-c:v", "libx264", "-b:v", str(target_bitrate),
             "-maxrate", str(target_bitrate), "-bufsize", str(target_bitrate // 2),
             "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out_path],
            capture_output=True, timeout=300
        )
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            os.unlink(path)
            return out_path
    except Exception as e:
        print(f"Compression error: {e}")
        if os.path.exists(out_path):
            os.unlink(out_path)
    return path


def get_video_info(path: str) -> dict:
    """Get video duration, width, height via ffprobe."""
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, text=True, timeout=30
        )
        data = __import__('json').loads(probe.stdout)
        duration = int(float(data.get("format", {}).get("duration", 0)))
        width = 0
        height = 0
        for s in data.get("streams", []):
            if s.get("codec_type") == "video":
                width = s.get("width", 0)
                height = s.get("height", 0)
                break
        return {"duration": duration, "width": width, "height": height}
    except Exception:
        return {"duration": 0, "width": 0, "height": 0}


def generate_thumbnail(path: str) -> str | None:
    """Generate a thumbnail from the video. Returns path or None."""
    thumb_path = path + ".thumb.jpg"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", path, "-ss", "00:00:01", "-vframes", "1",
             "-vf", "scale=320:-1", thumb_path],
            capture_output=True, timeout=30
        )
        if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
            return thumb_path
    except Exception:
        pass
    if os.path.exists(thumb_path):
        os.unlink(thumb_path)
    return None


def send_fallback(bot, message, post_url: str):
    """Fallback: send text links when media download fails."""
    origin_post_url = post_url
    post_url_clean = post_url.split('/?')[0]

    if USE_DACOGRAM:
        endpoint = 'www.dacogram.com'
    else:
        endpoint = 'ddinstagram.com'

    proxy_url = post_url_clean.replace('instagram.com', endpoint).replace('www.' + endpoint, endpoint)

    bot.send_message(
        chat_id=message.chat.id,
        text=f"[Reel]({origin_post_url})[.]({proxy_url}){HASHTAG}",
        reply_to_message_id=message.message_id,
        parse_mode="Markdown",
        disable_web_page_preview=False
    )

    if DELETE_ORIGINAL_MESSAGE:
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except Exception as e:
            print(f"Could not delete message: {e}")


def check_dacogram(post_url: str) -> bool:
    """Check if dacogram can embed this reel as video (og:video present)."""
    try:
        clean_url = post_url.split('/?')[0]
        dacogram_url = clean_url.replace('instagram.com', 'www.dacogram.com').replace('www.www.', 'www.')
        resp = requests.get(dacogram_url, timeout=8, allow_redirects=True)
        return 'og:video' in resp.text
    except Exception:
        return False


def send_dacogram_embed(bot, message, post_url: str):
    """Send message with dacogram embed link."""
    clean_url = post_url.split('/?')[0]
    dacogram_url = clean_url.replace('instagram.com', 'www.dacogram.com').replace('www.www.', 'www.')

    bot.send_message(
        chat_id=message.chat.id,
        text=f"[Reel]({post_url})[.]({dacogram_url}){HASHTAG}",
        reply_to_message_id=message.message_id,
        parse_mode="Markdown",
        disable_web_page_preview=False
    )

    if DELETE_ORIGINAL_MESSAGE:
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except Exception as e:
            print(f"Could not delete message: {e}")


def process_instagram_post(bot, message, post_url: str):
    try:
        chat_id = message.chat.id

        # Try dacogram first (fast, lightweight).
        if check_dacogram(post_url):
            send_dacogram_embed(bot, message, post_url)
            return

        # Dacogram unavailable — download via igram.
        bot.send_chat_action(chat_id, 'upload_video')

        media_urls = resolve_via_igram(post_url)
        if not media_urls:
            bot.set_message_reaction(chat_id, message.id, reaction=[telebot.types.ReactionTypeEmoji("💔")])
            send_fallback(bot, message, post_url)
            return

        # Single media.
        if len(media_urls) == 1:
            path = download_file(media_urls[0])
            if not path:
                send_fallback(bot, message, post_url)
                return
            path = compress_video(path)
            info = get_video_info(path)
            thumb_path = generate_thumbnail(path)
            try:
                caption = f"[Reel]({post_url}){HASHTAG}"
                thumb_file = open(thumb_path, 'rb') if thumb_path else None
                with open(path, 'rb') as f:
                    bot.send_video(
                        chat_id=chat_id,
                        video=f,
                        caption=caption,
                        parse_mode="Markdown",
                        reply_to_message_id=message.message_id,
                        duration=info["duration"] or None,
                        width=info["width"] or None,
                        height=info["height"] or None,
                        thumbnail=thumb_file,
                        supports_streaming=True,
                    )
                if thumb_file:
                    thumb_file.close()
            finally:
                os.unlink(path)
                if thumb_path and os.path.exists(thumb_path):
                    os.unlink(thumb_path)
        else:
            # Multiple media — send as media group.
            paths = []
            media_group = []
            for i, url in enumerate(media_urls):
                path = download_file(url)
                if not path:
                    continue
                path = compress_video(path)
                paths.append(path)
                item = telebot.types.InputMediaVideo(open(path, 'rb'))
                if i == 0:
                    item.caption = f"[Reel]({post_url}){HASHTAG}"
                    item.parse_mode = "Markdown"
                media_group.append(item)

            if media_group:
                bot.send_media_group(
                    chat_id=chat_id,
                    media=media_group,
                    reply_to_message_id=message.message_id,
                )
            else:
                send_fallback(bot, message, post_url)

            for path in paths:
                os.unlink(path)

        if DELETE_ORIGINAL_MESSAGE:
            try:
                bot.delete_message(chat_id, message.message_id)
            except Exception as e:
                print(f"Could not delete message: {e}")

    except Exception as e:
        print(f"Error processing Instagram post: {e}")
        try:
            send_fallback(bot, message, post_url)
        except:
            bot.reply_to(message, f"An error occurred: {str(e)}", disable_notification=True)


def process_tiktok_post(bot, message, post_url: str):
    try:
        fixed_url = post_url.replace('vm.tiktok.com', 'd.tnktok.com').replace('vt.tiktok.com', 'd.tnktok.com').replace('tiktok.com', 'd.tnktok.com')

        bot.send_message(
            chat_id=message.chat.id,
            text=f"[TikTok]({post_url})[.]({fixed_url})",
            reply_to_message_id=message.message_id,
            parse_mode="Markdown",
            disable_web_page_preview=False
        )

        if DELETE_ORIGINAL_MESSAGE:
            try:
                bot.delete_message(message.chat.id, message.message_id)
            except Exception as e:
                print(f"Could not delete message: {e}")

    except Exception as e:
        bot.reply_to(message, f"An error occurred: {str(e)}", disable_notification=True)
