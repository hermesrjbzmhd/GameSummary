import os
import re
import json
import time
import threading
import requests
from bs4 import BeautifulSoup
from http.server import HTTPServer, BaseHTTPRequestHandler

# ==================== ENVIRONMENT CONFIGURATION ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHANNEL_ID = os.getenv("CHANNEL_ID", "-100xxxxxxxxxx")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "3600"))  # in seconds (default: 1 hour)
PORT = int(os.getenv("PORT", 8000))  # HTTP server port for cloud health checks

TEAMS_FILE = os.getenv("TEAMS_FILE", "teams.txt")
HISTORY_FILE = os.getenv("HISTORY_FILE", "sent_videos.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
# ===================================================================


# ---------- LIGHTWEIGHT HTTP SERVER FOR CLOUD HEALTH CHECKS ----------
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Varzesh3 Highlights Bot is running successfully!")

    def log_message(self, format, *args):
        return  # Suppress HTTP request logs to keep terminal clean


def run_health_check_server():
    """Starts a lightweight background HTTP server on the configured port."""
    try:
        server = HTTPServer(("0.0.0.0", PORT), HealthCheckHandler)
        server.serve_forever()
    except Exception as e:
        print(f"[-] Error starting health check server: {e}")
# ---------------------------------------------------------------------


def validate_config():
    """Validates required environment variables."""
    if not BOT_TOKEN or "YOUR_BOT_TOKEN" in BOT_TOKEN:
        print("[!] Error: BOT_TOKEN environment variable is not configured.")
        return False
    if not CHANNEL_ID or "-100xxxxxxxxxx" in CHANNEL_ID:
        print("[!] Error: CHANNEL_ID environment variable is not configured.")
        return False
    return True


def normalize_text(text):
    """Normalizes Persian/Arabic characters and whitespace for accurate matching."""
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
    """Checks if the title matches highlight keywords and an allowed team."""
    normalized_title = normalize_text(title)

    if not any(kw in normalized_title for kw in ["خلاصه", "kholase", "highlights"]):
        return False, None

    for team in allowed_teams:
        if team in normalized_title:
            return True, team

    return False, None


def extract_all_mp4_qualities(page_url):
    """
    Scrapes the video page and returns a dictionary of categorized quality links:
    e.g., {'1080p': 'url', '720p': 'url', '480p': 'url', ...}
    """
    try:
        res = requests.get(page_url, headers=HEADERS, timeout=15)
        if res.status_code != 200:
            return {}

        soup = BeautifulSoup(res.text, "html.parser")
        found_urls = set()

        # 1. Extract from <source> and <video> tags
        for src in soup.find_all(["source", "video"]):
            if src.get("src") and ".mp4" in src["src"]:
                found_urls.add(src["src"])

        # 2. Extract from download anchor <a> tags
        for a in soup.find_all("a", href=True):
            if ".mp4" in a["href"]:
                found_urls.add(a["href"])

        # 3. Extract via Regex from entire HTML
        mp4_matches = re.findall(r'https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*', res.text)
        for url in mp4_matches:
            found_urls.add(url)

        qualities_dict = {}

        # Categorize by quality label
        for url in found_urls:
            u_lower = url.lower()
            if "1080" in u_lower or "fhd" in u_lower:
                qualities_dict["1080p FHD"] = url
            elif "720" in u_lower or "hd" in u_lower or "hq" in u_lower:
                qualities_dict["720p HD"] = url
            elif "480" in u_lower or "sd" in u_lower:
                qualities_dict["480p"] = url
            elif "360" in u_lower:
                qualities_dict["360p"] = url
            elif "240" in u_lower:
                qualities_dict["240p"] = url
            else:
                if "Direct Link" not in qualities_dict:
                    qualities_dict["Direct Link"] = url

        return qualities_dict

    except Exception as e:
        print(f"[-] Error extracting qualities from {page_url}: {e}")
        return {}


def build_caption(title, qualities_dict, page_url):
    """Formats the Telegram caption with structured quality download links."""
    caption_lines = [f"⚽️ <b>{title}</b>\n"]

    if qualities_dict:
        caption_lines.append("📥 <b>Download Links:</b>")
        for label, url in qualities_dict.items():
            caption_lines.append(f"▫️ <a href='{url}'>{label}</a>")
        caption_lines.append("")

    caption_lines.append(f"🔗 <a href='{page_url}'>Watch on Varzesh3</a>")
    return "\n".join(caption_lines)


def upload_video_file_to_telegram(qualities_dict, title, page_url):
    """
    Selects the highest available quality under 50MB, downloads it,
    and uploads the video file directly to the Telegram channel.
    """
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo"
    text_caption = build_caption(title, qualities_dict, page_url)

    # Sort quality URLs from highest priority to lowest
    def get_priority(item):
        label = item[0].lower()
        if "1080" in label:
            return 4
        if "720" in label:
            return 3
        if "480" in label:
            return 2
        if "360" in label:
            return 1
        return 0

    sorted_qualities = sorted(qualities_dict.items(), key=get_priority, reverse=True)

    for label, mp4_url in sorted_qualities:
        temp_filename = f"temp_{int(time.time())}.mp4"
        try:
            print(f"[*] Checking stream quality [{label}]: {mp4_url}")
            with requests.get(mp4_url, headers=HEADERS, stream=True, timeout=60) as r:
                r.raise_for_status()

                # Telegram standard bot upload limit is 50MB
                content_length = r.headers.get("content-length")
                if content_length:
                    size_mb = int(content_length) / (1024 * 1024)
                    print(f"[*] Detected file size: {size_mb:.2f} MB")
                    if size_mb > 49.5:
                        print(f"[-] Quality [{label}] exceeds 50MB Telegram limit. Trying next quality...")
                        continue

                # Stream-download selected quality
                with open(temp_filename, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)

            print(f"[*] Uploading video file [{label}] to Telegram channel...")
            with open(temp_filename, "rb") as video_file:
                files = {"video": video_file}
                data = {
                    "chat_id": CHANNEL_ID,
                    "caption": text_caption,
                    "parse_mode": "HTML",
                    "supports_streaming": True
                }
                response = requests.post(api_url, data=data, files=files, timeout=180)
                res_data = response.json()

            if res_data.get("ok"):
                print(f"[✓] Successfully uploaded video with quality [{label}].")
                return True
            else:
                print(f"[-] Telegram API error: {res_data.get('description')}")

        except Exception as e:
            print(f"[-] Error processing quality [{label}]: {e}")

        finally:
            if os.path.exists(temp_filename):
                try:
                    os.remove(temp_filename)
                except Exception:
                    pass

    # Fallback to text message if all video uploads exceed limit or fail
    return fallback_send_text(title, qualities_dict, page_url)


def fallback_send_text(title, qualities_dict, page_url):
    """Sends a text message with download links if video upload fails."""
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": build_caption(title, qualities_dict, page_url),
        "parse_mode": "HTML"
    }
    try:
        res = requests.post(api_url, json=payload, timeout=20)
        return res.json().get("ok", False)
    except Exception:
        return False


def process_varzesh3():
    """Scrapes Varzesh3 video homepage and uploads new highlight videos."""
    target_url = "https://video.varzesh3.com"
    sent_videos = load_sent_videos()
    target_teams = load_target_teams()

    print(f"[*] Scanning Varzesh3 videos (Monitored teams count: {len(target_teams)})...")

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
            if isinstance(href, list):
                href = href[0] if href else ""
            href = str(href)
            title = link.get_text(strip=True)

            if "/video/" in href and title:
                is_matched, matched_team = is_matching_team(title, target_teams)

                if is_matched:
                    full_page_url = href if href.startswith("http") else f"https://video.varzesh3.com{href}"

                    if full_page_url not in sent_videos:
                        print(f"\n[+] Found new highlight for '{matched_team}': {title}")

                        qualities_dict = extract_all_mp4_qualities(full_page_url)

                        if qualities_dict:
                            success = upload_video_file_to_telegram(qualities_dict, title, full_page_url)
                        else:
                            success = fallback_send_text(title, qualities_dict, full_page_url)

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

    # Single-run mode for GitHub Actions
    if os.getenv("RUN_ONCE") == "true":
        print("=== Running in Single-Execution Mode (GitHub Actions) ===")
        process_varzesh3()
    else:
        # Continuous daemon mode for local machine or dedicated servers
        threading.Thread(target=run_health_check_server, daemon=True).start()
        print(f"[*] Health check server active on port {PORT}.")
        print("=== Varzesh3 Highlights Bot started successfully ===")
        while True:
            process_varzesh3()
            print(f"[*] Sleeping for {CHECK_INTERVAL // 60} minutes...\n")
            time.sleep(CHECK_INTERVAL)