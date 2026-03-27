#!./myvenv/bin/python3

import math
import os
import sys
import subprocess
import requests
import telebot
import tempfile
from dotenv import load_dotenv
from PIL import Image

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


def resolve_via_igram(post_url: str) -> list[dict]:
    """Resolve Instagram URL to direct media items via igram.world.
    Returns list of dicts: [{"url": ..., "type": "video"|"photo"}, ...]
    """
    try:
        from igram_resolver.igram_resolver import resolve
        urls = resolve(post_url)
    except Exception as e:
        print(f"igram resolver error: {e}")
        return []

    items = []
    for url in urls:
        is_video = '.mp4' in url or 'video' in url
        items.append({"url": url, "type": "video" if is_video else "photo"})
    return items


def download_file(url: str, suffix: str = ".mp4") -> str | None:
    """Download a file to a temp path. Returns path or None."""
    try:
        resp = requests.get(url, stream=True, timeout=120)
        resp.raise_for_status()
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        for chunk in resp.iter_content(chunk_size=8192):
            tmp.write(chunk)
        tmp.close()
        return tmp.name
    except Exception as e:
        print(f"Download error: {e}")
        return None


def make_collage(image_paths: list[str]) -> str | None:
    """Create a collage from multiple images. Returns path to collage jpg."""
    if not image_paths:
        return None
    if len(image_paths) == 1:
        return image_paths[0]

    images = []
    for p in image_paths:
        try:
            images.append(Image.open(p))
        except Exception:
            pass
    if not images:
        return None

    n = len(images)
    cols = min(n, 2) if n <= 4 else min(n, 3)
    rows = math.ceil(n / cols)

    # Target cell size.
    cell_w = max(img.width for img in images)
    cell_h = max(img.height for img in images)
    # Cap to reasonable size.
    cell_w = min(cell_w, 1080)
    cell_h = min(cell_h, 1080)

    gap = 4
    canvas_w = cols * cell_w + (cols - 1) * gap
    canvas_h = rows * cell_h + (rows - 1) * gap
    canvas = Image.new('RGB', (canvas_w, canvas_h), (255, 255, 255))

    for idx, img in enumerate(images):
        row = idx // cols
        col = idx % cols
        # Resize to fit cell, maintaining aspect ratio.
        img.thumbnail((cell_w, cell_h), Image.LANCZOS)
        x = col * (cell_w + gap) + (cell_w - img.width) // 2
        y = row * (cell_h + gap) + (cell_h - img.height) // 2
        canvas.paste(img, (x, y))

    out_path = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False).name
    canvas.save(out_path, "JPEG", quality=90)
    for img in images:
        img.close()
    return out_path


def compress_video(path: str) -> str:
    """Compress video to fit under MAX_FILE_SIZE. Returns path (same or new)."""
    size = os.path.getsize(path)
    if size <= MAX_FILE_SIZE:
        return path

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
    """Fallback: send just the Instagram link when nothing else works."""
    bot.send_message(
        chat_id=message.chat.id,
        text=f"[Post]({post_url}){HASHTAG}",
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

        # Try dacogram first for reels (fast, lightweight).
        if '/reel/' in post_url and check_dacogram(post_url):
            send_dacogram_embed(bot, message, post_url)
            return

        # Dacogram unavailable — download via igram.
        bot.send_chat_action(chat_id, 'upload_video')

        media_items = resolve_via_igram(post_url)
        if not media_items:
            bot.set_message_reaction(chat_id, message.id, reaction=[telebot.types.ReactionTypeEmoji("💔")])
            send_fallback(bot, message, post_url)
            return

        has_video = any(m['type'] == 'video' for m in media_items)
        photos_only = all(m['type'] == 'photo' for m in media_items)
        caption = f"[Post]({post_url}){HASHTAG}"

        if photos_only and len(media_items) > 1:
            # Multiple photos — download all and make collage.
            bot.send_chat_action(chat_id, 'upload_photo')
            paths = []
            for item in media_items:
                p = download_file(item['url'], suffix=".jpg")
                if p:
                    paths.append(p)
            if not paths:
                send_fallback(bot, message, post_url)
                return
            collage_path = make_collage(paths)
            try:
                with open(collage_path, 'rb') as f:
                    bot.send_photo(
                        chat_id=chat_id,
                        photo=f,
                        caption=caption,
                        parse_mode="Markdown",
                        reply_to_message_id=message.message_id,
                    )
            finally:
                for p in paths:
                    if os.path.exists(p):
                        os.unlink(p)
                if collage_path and collage_path not in paths and os.path.exists(collage_path):
                    os.unlink(collage_path)

        elif photos_only and len(media_items) == 1:
            # Single photo.
            bot.send_chat_action(chat_id, 'upload_photo')
            path = download_file(media_items[0]['url'], suffix=".jpg")
            if not path:
                send_fallback(bot, message, post_url)
                return
            try:
                with open(path, 'rb') as f:
                    bot.send_photo(
                        chat_id=chat_id,
                        photo=f,
                        caption=caption,
                        parse_mode="Markdown",
                        reply_to_message_id=message.message_id,
                    )
            finally:
                os.unlink(path)

        elif len(media_items) == 1:
            # Single video.
            path = download_file(media_items[0]['url'])
            if not path:
                send_fallback(bot, message, post_url)
                return
            path = compress_video(path)
            info = get_video_info(path)
            thumb_path = generate_thumbnail(path)
            try:
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
            # Multiple media (mixed or multiple videos) — send as media group.
            paths = []
            media_group = []
            for i, item in enumerate(media_items):
                suffix = ".mp4" if item['type'] == 'video' else ".jpg"
                path = download_file(item['url'], suffix=suffix)
                if not path:
                    continue
                if item['type'] == 'video':
                    path = compress_video(path)
                    paths.append(path)
                    mg_item = telebot.types.InputMediaVideo(open(path, 'rb'))
                else:
                    paths.append(path)
                    mg_item = telebot.types.InputMediaPhoto(open(path, 'rb'))
                if i == 0:
                    mg_item.caption = caption
                    mg_item.parse_mode = "Markdown"
                media_group.append(mg_item)

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
