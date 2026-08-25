import os
import re
import json
import time
import threading
import requests
from bs4 import BeautifulSoup
from http.server import HTTPServer, BaseHTTPRequestHandler

# ==================== ENVIRONMENT CONFIGURATION ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "3600"))  # in seconds (default: 1 hour)
PORT = int(os.getenv("PORT", 8000))  # HTTP server port for cloud health checks

TEAMS_FILE = os.getenv("TEAMS_FILE", "teams.txt")
HISTORY_FILE = os.getenv("HISTORY_FILE", "sent_videos.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
# ===================================================================


# ---------- DUMMY HTTP SERVER FOR CLOUD HEALTH CHECKS ----------
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Varzesh3 Highlights Bot is running successfully!")

    def log_message(self, format, *args):
        return  # Suppress default HTTP request logs to keep console clean


def run_health_check_server():
    """Starts a lightweight HTTP server on the configured port."""
    try:
        server = HTTPServer(("0.0.0.0", PORT), HealthCheckHandler)
        server.serve_forever()
    except Exception as e:
        print(f"[-] Error starting health check server: {e}")
# ----------------------------------------------------------------


def validate_config():
    """Validates that necessary environment variables are set."""
    if not BOT_TOKEN or "YOUR_BOT_TOKEN" in BOT_TOKEN:
        print("[!] Error: BOT_TOKEN environment variable is not configured.")
        return False
    if not CHANNEL_ID or "-100xxxxxxxxxx" in CHANNEL_ID:
        print("[!] Error: CHANNEL_ID environment variable is not configured.")
        return False
    return True


def normalize_text(text):
    """Normalizes Persian/Arabic characters and spacing for accurate matching."""
    if not text:
        return ""
    text = text.replace("ي", "ی").replace("ك", "ک").replace("‌", " ")
    return " ".join(text.split()).lower()


def load_target_teams():
    """Loads target team names from the teams.txt file."""
    if not os.path.exists(TEAMS_FILE):
        default_teams = ["پرسپولیس", "استقلال", "رئال مادرید", "بارسلونا"]
        with open(TEAMS_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(default_teams))
        return [normalize_text(t) for t in default_teams]

    teams = []
    with open(TEAMS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            cleaned = line.strip()
            if cleaned and not cleaned.startswith("#"):
                teams.append(normalize_text(cleaned))
    return teams


def load_sent_videos():
    """Loads previously sent video URLs to prevent duplicates."""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_sent_videos(sent_list):
    """Saves sent video URLs to the history JSON file."""
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(list(sent_list), f, ensure_ascii=False, indent=2)


def is_matching_team(title, allowed_teams):
    """Checks if the video title contains highlight keywords and a target team."""
    normalized_title = normalize_text(title)

    # Ensure it's a match highlight video
    if not any(kw in normalized_title for kw in ["خلاصه", "kholase", "highlights"]):
        return False, None

    # Check for team names
    for team in allowed_teams:
        if team in normalized_title:
            return True, team

    return False, None


def get_direct_mp4_url(page_url):
    """Scrapes and extracts the direct .mp4 download URL from a video page."""
    try:
        res = requests.get(page_url, headers=HEADERS, timeout=15)
        if res.status_code != 200:
            return None

        soup = BeautifulSoup(res.text, "html.parser")

        # 1. Check OpenGraph / Twitter video meta tags
        for meta_prop in ["og:video", "og:video:url", "og:video:secure_url", "twitter:player:stream"]:
            meta = soup.find("meta", property=meta_prop) or soup.find("meta", attrs={"name": meta_prop})
            if meta and meta.get("content") and ".mp4" in meta["content"]:
                return meta["content"]

        # 2. Check HTML5 <video> and <source> tags
        video_tag = soup.find("video")
        if video_tag:
            source_tag = video_tag.find("source")
            if source_tag and source_tag.get("src"):
                return source_tag["src"]
            if video_tag.get("src"):
                return video_tag["src"]

        # 3. Check <a> download tags
        for a in soup.find_all("a", href=True):
            if ".mp4" in a["href"]:
                return a["href"]

        # 4. Search with Regex in the raw HTML response
        mp4_matches = re.findall(r'https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*', res.text)
        if mp4_matches:
            return mp4_matches[0]

    except Exception as e:
        print(f"[-] Error extracting MP4 link from {page_url}: {e}")

    return None


def upload_video_file_to_telegram(mp4_url, caption, page_url):
    """Downloads video temporarily and uploads it as a video file to Telegram."""
    temp_filename = f"temp_{int(time.time())}.mp4"
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo"

    try:
        print("[*] Downloading video from source server...")
        with requests.get(mp4_url, headers=HEADERS, stream=True, timeout=60) as r:
            r.raise_for_status()

            # Telegram Bot API standard file upload limit: 50MB
            content_length = r.headers.get("content-length")
            if content_length and int(content_length) > 50 * 1024 * 1024:
                print("[-] Video size exceeds 50MB; falling back to link message...")
                return fallback_send_text(caption, page_url)

            with open(temp_filename, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)

        print("[*] Uploading video file to Telegram channel...")
        text_caption = f"⚽️ <b>{caption}</b>\n\n🔗 <a href='{page_url}'>مشاهده در ورزش سه</a>"

        with open(temp_filename, "rb") as video_file:
            files = {"video": video_file}
            data = {
                "chat_id": CHANNEL_ID,
                "caption": text_caption,
                "parse_mode": "HTML",
                "supports_streaming": True
            }
            response = requests.post(api_url, data=data, files=files, timeout=120)
            res_data = response.json()

        if res_data.get("ok"):
            print("[✓] Video uploaded successfully.")
            return True
        else:
            print(f"[-] Telegram API error: {res_data.get('description')}")
            return fallback_send_text(caption, page_url)

    except Exception as e:
        print(f"[-] Exception during video processing/upload: {e}")
        return fallback_send_text(caption, page_url)

    finally:
        # Clean up temporary video file
        if os.path.exists(temp_filename):
            try:
                os.remove(temp_filename)
            except Exception:
                pass


def fallback_send_text(caption, page_url):
    """Sends a text message with a clickable URL if direct video upload fails."""
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": f"⚽️ <b>{caption}</b>\n\n🔗 <a href='{page_url}'>برای تماشای ویدیو کلیک کنید</a>",
        "parse_mode": "HTML"
    }
    try:
        res = requests.post(api_url, json=payload, timeout=20)
        return res.json().get("ok", False)
    except Exception:
        return False


def process_varzesh3():
    """Scrapes Varzesh3 video homepage and processes matching highlight videos."""
    target_url = "https://video.varzesh3.com"
    sent_videos = load_sent_videos()
    target_teams = load_target_teams()

    print(f"[*] Scanning Varzesh3 videos (Target teams count: {len(target_teams)})...")

    try:
        res = requests.get(target_url, headers=HEADERS, timeout=20)
        if res.status_code != 200:
            print(f"[-] Failed to load main page. HTTP Status: {res.status_code}")
            return

        soup = BeautifulSoup(res.text, "html.parser")
        links = soup.find_all("a", href=True)
        found_new = 0

        for link in links:
            href = link.get("href", "")
            href_value = href[0] if isinstance(href, list) else str(href)
            title = link.get_text(strip=True)

            if "/video/" in href_value and title:
                is_matched, matched_team = is_matching_team(title, target_teams)

                if is_matched:
                    full_page_url = href_value if href_value.startswith("http") else f"https://video.varzesh3.com{href_value}"

                    if full_page_url not in sent_videos:
                        print(f"\n[+] Found new highlight for '{matched_team}': {title}")

                        mp4_url = get_direct_mp4_url(full_page_url)

                        if mp4_url:
                            success = upload_video_file_to_telegram(mp4_url, title, full_page_url)
                        else:
                            success = fallback_send_text(title, full_page_url)

                        if success:
                            sent_videos.add(full_page_url)
                            save_sent_videos(sent_videos)
                            found_new += 1
                            time.sleep(5)

        if found_new == 0:
            print("[*] No new highlights matching your target teams were found.")

    except Exception as e:
        print(f"[-] Scraping error: {e}")


# ==================== MAIN ENTRY POINT ====================
if __name__ == "__main__":
    if not validate_config():
        exit(1)

    # اگر روی GitHub Actions باشد، یک‌بار اجرا شده و خارج می‌شود
    if os.getenv("RUN_ONCE") == "true":
        print("=== Running in Single-Execution Mode (GitHub Actions) ===")
        process_varzesh3()
    else:
        # حالت حلقه مداوم برای سیستم محلی
        threading.Thread(target=run_health_check_server, daemon=True).start()
        print("=== Varzesh3 Highlights Bot started successfully ===")
        while True:
            process_varzesh3()
            print(f"[*] Sleeping for {CHECK_INTERVAL // 60} minutes...\n")
            time.sleep(CHECK_INTERVAL)