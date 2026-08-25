import os
import re
import json
import time
import threading
import subprocess
import requests
from bs4 import BeautifulSoup
from http.server import HTTPServer, BaseHTTPRequestHandler

# ==================== ENVIRONMENT CONFIGURATION ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHANNEL_ID = os.getenv("CHANNEL_ID", "-100xxxxxxxxxx")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "3600"))
PORT = int(os.getenv("PORT", 8000))
MAX_VIDEOS_PER_RUN = 3  # Process maximum 3 videos per run to prevent timeout

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
        self.wfile.write(b"Varzesh3 Bot is running!")

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
        print("[!] Error: BOT_TOKEN is missing.")
        return False
    if not CHANNEL_ID or "-100xxxxxxxxxx" in CHANNEL_ID:
        print("[!] Error: CHANNEL_ID is missing.")
        return False
    return True


def normalize_text(text):
    """Normalizes Persian/Arabic characters and spaces for matching."""
    if not text:
        return ""
    text = text.replace("ي", "ی").replace("ك", "ک").replace("‌", " ")
    return " ".join(text.split()).lower()


def clean_title(link_tag):
    """Extracts a clean, non-duplicated title from a Varzesh3 video card."""
    raw_title = link_tag.get("title", "").strip()
    if not raw_title:
        h_tag = link_tag.find(["h2", "h3", "h4"])
        if h_tag:
            raw_title = h_tag.get_text(strip=True)
        else:
            raw_title = link_tag.get_text(" ", strip=True)

    parts = re.split(r'(?=خلاصه)', raw_title)
    if len(parts) > 1 and parts[0].strip():
        raw_title = parts[0].strip()
    elif len(parts) > 1 and not parts[0].strip():
        raw_title = parts[1].strip()

    return re.sub(r'\s+', ' ', raw_title).strip()


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
    """Cleans up escaped characters and forces valid HTTPS URL."""
    if not raw_url:
        return ""
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
    """Scrapes the video detail page and extracts all available resolutions."""
    qualities_dict = {}

    try:
        res = requests.get(page_url, headers=HEADERS, timeout=12)
        if res.status_code != 200:
            print(f"[-] Failed to fetch page. HTTP Code: {res.status_code}")
            return {}

        soup = BeautifulSoup(res.text, "html.parser")
        raw_matches = set()

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if ".mp4" in href.lower():
                raw_matches.add((clean_url(href), a.get_text(strip=True)))

        for tag in soup.find_all(["source", "video"]):
            src = tag.get("src")
            if src and ".mp4" in src.lower():
                raw_matches.add((clean_url(src), tag.get("label", "")))

        regex_matches = re.findall(r'https?(?::|\\/|/)+[^\s"\'<>]+\.mp4[^\s"\'<>]*', res.text)
        for r_url in regex_matches:
            raw_matches.add((clean_url(r_url), ""))

        for url, text in raw_matches:
            if not url or ".mp4" not in url.lower():
                continue

            combined_str = f"{url.lower()} {text.lower()}"

            if ("/videos/" in url and "/videos-quality/" not in url) or ("720" in combined_str or "hd" in combined_str):
                if "720p HD" not in qualities_dict:
                    qualities_dict["720p HD"] = url
            elif "1080" in combined_str or "fhd" in combined_str:
                if "1080p FHD" not in qualities_dict:
                    qualities_dict["1080p FHD"] = url
            elif "480" in combined_str or "sd" in combined_str:
                if "480p" not in qualities_dict:
                    qualities_dict["480p"] = url
            elif "360" in combined_str:
                if "360p" not in qualities_dict:
                    qualities_dict["360p"] = url
            else:
                if "Direct Link" not in qualities_dict:
                    qualities_dict["Direct Link"] = url

        return qualities_dict

    except Exception as e:
        print(f"[-] Error extracting qualities from {page_url}: {e}")
        return {}


def build_caption(title, qualities_dict, page_url, quality_label=""):
    """Builds formatted Telegram HTML caption with direct download links."""
    lines = [f"⚽️ <b>{title}</b>"]
    if quality_label:
        lines.append(f"🎬 <b>Quality:</b> {quality_label}\n")
    else:
        lines.append("")

    if qualities_dict:
        lines.append("📥 <b>Download Links:</b>")
        for label, url in qualities_dict.items():
            lines.append(f"▫️ <a href='{url}'>{label}</a>")
        lines.append("")

    lines.append(f"🔗 <a href='{page_url}'>Watch on Varzesh3</a>")
    return "\n".join(lines)


def get_stream_file_size_mb(url):
    """Quickly checks file size via HEAD request."""
    try:
        r = requests.head(url, headers=HEADERS, timeout=6, allow_redirects=True)
        length = r.headers.get("content-length")
        if length:
            return int(length) / (1024 * 1024)
    except Exception:
        pass
    return None


def get_video_duration_seconds(input_path):
    """Gets duration in seconds using ffprobe with a strict 15s timeout."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            input_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=True)
        return float(res.stdout.strip())
    except Exception:
        return None


def fast_compress_video(input_path, output_path, target_mb=45.0):
    """Calculates optimal bitrate and compresses video with a strict 120s timeout."""
    try:
        duration = get_video_duration_seconds(input_path)

        if duration and duration > 0:
            print(f"[*] Video duration: {duration:.1f}s ({duration/60:.1f} min)")
            target_total_kbits = target_mb * 8192
            audio_bitrate_kbps = 64
            target_video_kbps = int((target_total_kbits / duration) - audio_bitrate_kbps)
            target_video_kbps = max(250, min(target_video_kbps, 1600))
            print(f"[*] Compressing with target video bitrate: {target_video_kbps} kbps")

            cmd = [
                "ffmpeg", "-y", "-i", input_path,
                "-vf", "scale=-2:min(720\\,ih)",
                "-c:v", "libx264",
                "-b:v", f"{target_video_kbps}k",
                "-maxrate", f"{int(target_video_kbps * 1.2)}k",
                "-bufsize", f"{int(target_video_kbps * 2)}k",
                "-preset", "ultrafast",
                "-threads", "0",
                "-c:a", "aac",
                "-b:a", f"{audio_bitrate_kbps}k",
                "-movflags", "+faststart",
                output_path
            ]
        else:
            print("[*] Using CRF 32 compression mode...")
            cmd = [
                "ffmpeg", "-y", "-i", input_path,
                "-vf", "scale=-2:480",
                "-c:v", "libx264", "-crf", "32",
                "-preset", "ultrafast",
                "-threads", "0",
                "-c:a", "aac", "-b:a", "64k",
                "-movflags", "+faststart",
                output_path
            ]

        # 120-second timeout prevents any hang
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120, check=True)

        if os.path.exists(output_path):
            final_size_mb = os.path.getsize(output_path) / (1024 * 1024)
            print(f"[✓] Compression finished! Result size: {final_size_mb:.2f} MB")
            return final_size_mb <= 49.0

    except subprocess.TimeoutExpired:
        print("[-] FFmpeg compression timed out after 120s.")
        return False
    except Exception as e:
        print(f"[-] Compression error: {e}")
        return False

    return False


def upload_video_file_to_telegram(qualities_dict, title, page_url):
    """Selects best stream, compresses if needed, and uploads to Telegram."""
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo"

    ready_direct_url = None
    ready_direct_label = None
    compress_candidate_url = None
    compress_candidate_label = None
    smallest_size_above_50 = 9999

    for label, url in qualities_dict.items():
        size_mb = get_stream_file_size_mb(url)
        if size_mb:
            print(f"[*] Stream [{label}] probe size: {size_mb:.2f} MB")
            if size_mb <= 49.0:
                if not ready_direct_url or "720" in label:
                    ready_direct_url = url
                    ready_direct_label = label
            else:
                if size_mb < smallest_size_above_50:
                    smallest_size_above_50 = size_mb
                    compress_candidate_url = url
                    compress_candidate_label = label

    if ready_direct_url:
        selected_url = ready_direct_url
        selected_label = ready_direct_label
        needs_compression = False
    elif compress_candidate_url:
        selected_url = compress_candidate_url
        selected_label = compress_candidate_label
        needs_compression = True
    else:
        selected_label, selected_url = list(qualities_dict.items())[0]
        needs_compression = True

    raw_temp_file = f"raw_{int(time.time())}.mp4"
    final_upload_file = f"final_{int(time.time())}.mp4"

    try:
        print(f"[*] Downloading stream [{selected_label}]...")
        # 60s timeout for download
        with requests.get(selected_url, headers=HEADERS, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(raw_temp_file, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)

        initial_size = os.path.getsize(raw_temp_file) / (1024 * 1024)
        print(f"[*] Download completed ({initial_size:.2f} MB).")

        if initial_size <= 49.0 and not needs_compression:
            upload_path = raw_temp_file
            display_label = selected_label
        else:
            success = fast_compress_video(raw_temp_file, final_upload_file)
            if success:
                upload_path = final_upload_file
                display_label = f"{selected_label} (Compressed HD)"
            else:
                print("[-] Video exceeds 50MB and compression failed.")
                return fallback_send_text(title, qualities_dict, page_url)

        upload_size = os.path.getsize(upload_path) / (1024 * 1024)
        print(f"[*] Uploading playable video ({upload_size:.2f} MB) to Telegram...")

        text_caption = build_caption(title, qualities_dict, page_url, quality_label=display_label)

        with open(upload_path, "rb") as video_file:
            files = {"video": ("highlight.mp4", video_file, "video/mp4")}
            data = {
                "chat_id": CHANNEL_ID,
                "caption": text_caption,
                "parse_mode": "HTML",
                "supports_streaming": "true"
            }
            response = requests.post(api_url, data=data, files=files, timeout=180)
            res_data = response.json()

        if res_data.get("ok"):
            print(f"[✓] Playable video uploaded successfully to Telegram.")
            return True
        else:
            print(f"[-] Telegram upload error: {res_data.get('description')}")
            return fallback_send_text(title, qualities_dict, page_url)

    except Exception as e:
        print(f"[-] Error processing video upload: {e}")
        return fallback_send_text(title, qualities_dict, page_url)

    finally:
        for f_path in [raw_temp_file, final_upload_file]:
            if os.path.exists(f_path):
                try:
                    os.remove(f_path)
                except Exception:
                    pass


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
    """Scrapes Varzesh3 highlights and uploads playable video to Telegram."""
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
            if found_new >= MAX_VIDEOS_PER_RUN:
                print(f"[*] Reached limit of {MAX_VIDEOS_PER_RUN} videos per run. Remaining will process next cycle.")
                break

            href = link["href"]
            title = clean_title(link)

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
                            time.sleep(3)

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