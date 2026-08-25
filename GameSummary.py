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
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "3600"))
PORT = int(os.getenv("PORT", 8000))

TEAMS_FILE = os.getenv("TEAMS_FILE", "teams.txt")
HISTORY_FILE = os.getenv("HISTORY_FILE", "sent_videos.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://video.varzesh3.com/",
    "Accept": "*/*"
}
# ===================================================================


class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Varzesh3 Highlights Bot is running!")

    def log_message(self, format, *args):
        return


def run_health_check_server():
    """Lightweight HTTP server for cloud platforms health checks."""
    try:
        server = HTTPServer(("0.0.0.0", PORT), HealthCheckHandler)
        server.serve_forever()
    except Exception as e:
        print(f"[-] Health check server error: {e}")


def validate_config():
    """Validates required environment variables."""
    if not BOT_TOKEN or "YOUR_BOT_TOKEN" in BOT_TOKEN:
        print("[!] Error: BOT_TOKEN is not configured.")
        return False
    if not CHANNEL_ID or "-100xxxxxxxxxx" in CHANNEL_ID:
        print("[!] Error: CHANNEL_ID is not configured.")
        return False
    return True


def normalize_text(text):
    """Normalizes Persian/Arabic characters and spaces for matching."""
    if not text:
        return ""
    text = text.replace("ي", "ی").replace("ك", "ک").replace("‌", " ")
    return " ".join(text.split()).lower()


def load_target_teams():
    """Loads monitored team names from teams.txt."""
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
    """Loads history of sent videos."""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_sent_videos(sent_list):
    """Saves updated history to sent_videos.json."""
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(list(sent_list), f, ensure_ascii=False, indent=2)


def is_matching_team(title, allowed_teams):
    """Checks if title is a highlight and contains an allowed team name."""
    normalized_title = normalize_text(title)

    if not any(kw in normalized_title for kw in ["خلاصه", "kholase", "highlights"]):
        return False, None

    for team in allowed_teams:
        if team in normalized_title:
            return True, team

    return False, None


def clean_url(raw_url):
    """
    Cleans up escaped characters, handles JSON slashes, and enforces HTTPS.
    Fixes broken links caused by '\\/' JSON escaping.
    """
    if not raw_url:
        return ""
    
    # Unescape JSON slashes and HTML entities
    url = raw_url.replace(r"\/", "/").replace("\\/", "/").replace("&amp;", "&")
    url = url.strip().strip('"').strip("'").strip(";").strip("\\")

    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("http://"):
        url = "https://" + url[7:]
    elif not url.startswith("http"):
        url = "https://" + url

    return url


def extract_all_mp4_qualities(page_url):
    """
    Scrapes the video detail page, finds all valid MP4 streams (HD and SD),
    cleans all URLs, and categorizes them by resolution.
    """
    qualities_dict = {}

    try:
        res = requests.get(page_url, headers=HEADERS, timeout=15)
        if res.status_code != 200:
            print(f"[-] Failed to fetch page. HTTP Code: {res.status_code}")
            return {}

        soup = BeautifulSoup(res.text, "html.parser")
        raw_matches = set()

        # 1. Look for anchor download links
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if ".mp4" in href.lower():
                raw_matches.add((clean_url(href), a.get_text(strip=True)))

        # 2. Look for HTML5 <video> and <source> tags
        for tag in soup.find_all(["source", "video"]):
            src = tag.get("src")
            if src and ".mp4" in src.lower():
                raw_matches.add((clean_url(src), tag.get("label", "")))

        # 3. Search raw HTML / Javascript / JSON blocks for MP4 links
        # Match both standard URLs and escaped JSON URLs
        regex_matches = re.findall(r'https?(?::|\\/|/)+[^\s"\'<>]+\.mp4[^\s"\'<>]*', res.text)
        for r_url in regex_matches:
            raw_matches.add((clean_url(r_url), ""))

        # Categorize into clean resolution labels
        for url, text in raw_matches:
            if not url or ".mp4" not in url.lower():
                continue

            combined_str = f"{url.lower()} {text.lower()}"

            # Original HD Video file (Varzesh3 uses /videos/ for master high quality)
            if "/videos/" in url and "/videos-quality/" not in url:
                if "720p HD (Original)" not in qualities_dict:
                    qualities_dict["720p HD (Original)"] = url
            elif ("1080" in combined_str or "fhd" in combined_str) and "1080p FHD" not in qualities_dict:
                qualities_dict["1080p FHD"] = url
            elif ("720" in combined_str or "hd" in combined_str) and "720p HD" not in qualities_dict:
                qualities_dict["720p HD"] = url
            elif ("480" in combined_str or "sd" in combined_str) and "480p" not in qualities_dict:
                qualities_dict["480p"] = url
            elif ("360" in combined_str) and "360p" not in qualities_dict:
                qualities_dict["360p"] = url
            elif ("240" in combined_str) and "240p" not in qualities_dict:
                qualities_dict["240p"] = url
            else:
                if "Direct Link" not in qualities_dict:
                    qualities_dict["Direct Link"] = url

        return qualities_dict

    except Exception as e:
        print(f"[-] Error extracting qualities from {page_url}: {e}")
        return {}


def build_caption(title, qualities_dict, page_url):
    """Builds an HTML formatted Telegram caption with clickable download links."""
    lines = [f"⚽️ <b>{title}</b>\n"]

    if qualities_dict:
        lines.append("📥 <b>Download Links:</b>")
        for label, url in qualities_dict.items():
            lines.append(f"▫️ <a href='{url}'>{label}</a>")
        lines.append("")

    lines.append(f"🔗 <a href='{page_url}'>Watch on Varzesh3</a>")
    return "\n".join(lines)


def upload_video_file_to_telegram(qualities_dict, title, page_url):
    """
    Attempts to download the highest quality video stream under 50MB
    and uploads the actual video file to the Telegram channel.
    """
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo"
    text_caption = build_caption(title, qualities_dict, page_url)

    # Priority sorting: Highest quality first
    def get_priority(item):
        label = item[0].lower()
        if "1080" in label:
            return 6
        if "original" in label or "720" in label:
            return 5
        if "480" in label:
            return 4
        if "360" in label:
            return 3
        if "240" in label:
            return 2
        return 1

    sorted_qualities = sorted(qualities_dict.items(), key=get_priority, reverse=True)

    for label, mp4_url in sorted_qualities:
        temp_filename = f"temp_{int(time.time())}.mp4"
        try:
            print(f"[*] Trying video stream [{label}]: {mp4_url}")

            with requests.get(mp4_url, headers=HEADERS, stream=True, timeout=60) as r:
                if r.status_code != 200:
                    print(f"[-] Download stream failed with HTTP {r.status_code}")
                    continue

                content_length = r.headers.get("content-length")
                if content_length:
                    size_mb = int(content_length) / (1024 * 1024)
                    print(f"[*] Stream size: {size_mb:.2f} MB")
                    if size_mb > 49.5:
                        print(f"[-] Stream [{label}] exceeds 50MB. Attempting next quality...")
                        continue

                with open(temp_filename, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)

            file_size_mb = os.path.getsize(temp_filename) / (1024 * 1024)
            print(f"[*] Download completed ({file_size_mb:.2f} MB). Uploading to Telegram...")

            with open(temp_filename, "rb") as video_file:
                files = {"video": video_file}
                data = {
                    "chat_id": CHANNEL_ID,
                    "caption": text_caption,
                    "parse_mode": "HTML",
                    "supports_streaming": True
                }
                response = requests.post(api_url, data=data, files=files, timeout=300)
                res_data = response.json()

            if res_data.get("ok"):
                print(f"[✓] Video uploaded successfully in [{label}] quality.")
                return True
            else:
                print(f"[-] Telegram upload error: {res_data.get('description')}")

        except Exception as e:
            print(f"[-] Error processing quality [{label}]: {e}")

        finally:
            if os.path.exists(temp_filename):
                try:
                    os.remove(temp_filename)
                except Exception:
                    pass

    # If all direct uploads fail or are > 50MB, fallback to text message
    print("[-] Could not upload video file. Falling back to text message...")
    return fallback_send_text(title, qualities_dict, page_url)


def fallback_send_text(title, qualities_dict, page_url):
    """Sends a text message with clean download links if video upload fails."""
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": build_caption(title, qualities_dict, page_url),
        "parse_mode": "HTML"
    }
    try:
        res = requests.post(api_url, json=payload, timeout=20)
        return res.json().get("ok", False)
    except Exception as e:
        print(f"[-] Fallback send error: {e}")
        return False


def process_varzesh3():
    """Scrapes Varzesh3 highlights and uploads them to Telegram."""
    target_url = "https://video.varzesh3.com"
    sent_videos = load_sent_videos()
    target_teams = load_target_teams()

    print(f"[*] Scanning Varzesh3 (Monitored teams: {len(target_teams)})...")

    try:
        res = requests.get(target_url, headers=HEADERS, timeout=20)
        if res.status_code != 200:
            print(f"[-] Failed to access Varzesh3 homepage. HTTP: {res.status_code}")
            return

        soup = BeautifulSoup(res.text, "html.parser")
        links = soup.find_all("a", href=True)
        found_new = 0

        for link in links:
            href = link["href"]
            title = link.get_text(strip=True)

            if "/video/" in href and title:
                is_matched, matched_team = is_matching_team(title, target_teams)

                if is_matched:
                    full_page_url = href if href.startswith("http") else f"https://video.varzesh3.com{href}"

                    if full_page_url not in sent_videos:
                        print(f"\n[+] Found highlight for team '{matched_team}': {title}")

                        qualities_dict = extract_all_mp4_qualities(full_page_url)
                        print(f"[*] Extracted valid qualities: {list(qualities_dict.keys())}")

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
            print("[*] No new highlights matching your team list were found.")

    except Exception as e:
        print(f"[-] Scraping error: {e}")


# ==================== MAIN ENTRY POINT ====================
if __name__ == "__main__":
    if not validate_config():
        exit(1)

    if os.getenv("RUN_ONCE") == "true":
        print("=== Running in Single-Execution Mode (GitHub Actions) ===")
        process_varzesh3()
    else:
        threading.Thread(target=run_health_check_server, daemon=True).start()
        print(f"[*] Health check server running on port {PORT}.")
        print("=== Varzesh3 Highlights Bot started successfully ===")
        while True:
            process_varzesh3()
            print(f"[*] Sleeping for {CHECK_INTERVAL // 60} minutes...\n")
            time.sleep(CHECK_INTERVAL)