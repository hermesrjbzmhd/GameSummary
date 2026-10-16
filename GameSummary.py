import os
import re
import json
import time
import threading
import subprocess
import unicodedata
import requests
from bs4 import BeautifulSoup
from http.server import HTTPServer, BaseHTTPRequestHandler

# ==================== ENVIRONMENT CONFIGURATION ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHANNEL_ID = os.getenv("CHANNEL_ID", "-100xxxxxxxxxx")
CHANNEL_2_ID = os.getenv("CHANNEL_2_ID", "")
CHANNEL_2_SIGNATURE = os.getenv("CHANNEL_2_SIGNATURE", "")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "3600"))
PORT = int(os.getenv("PORT", 8000))
MAX_VIDEOS_PER_RUN = 5  # Process maximum 5 videos per run to prevent timeout

TEAMS_FILE = os.getenv("TEAMS_FILE", "teams.txt")
HISTORY_FILE = os.getenv("HISTORY_FILE", "sent_videos.json")
CHANNELS_FILE = os.getenv("CHANNELS_FILE", "channels.json")
CONFIG_FILE = os.getenv("CONFIG_FILE", "config.json")
WORKFLOW_FILE = os.getenv("WORKFLOW_FILE", ".github/workflows/bot.yaml")
ADMIN_ID = os.getenv("ADMIN_ID", "")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://video.varzesh3.com/",
    "Accept": "*/*"
}
# ===================================================================


def load_config():
    """Loads configuration from config.json."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_config(cfg):
    """Saves configuration to config.json."""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def get_admin_id():
    """Returns admin Telegram ID from environment or config.json."""
    if ADMIN_ID:
        return str(ADMIN_ID).strip()
    cfg = load_config()
    return str(cfg.get("admin_id", "")).strip()


def load_channels():
    """Loads configured channels from channels.json or environment variables."""
    channels = []
    if os.path.exists(CHANNELS_FILE):
        try:
            with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, list):
                    channels = loaded
        except Exception as e:
            print(f"[-] Error reading {CHANNELS_FILE}: {e}")

    existing_ids = {str(c.get("id")) for c in channels if c.get("id")}

    # Default Channel 1 (Full format)
    if CHANNEL_ID and CHANNEL_ID not in existing_ids and "-100xxxxxxxxxx" not in CHANNEL_ID:
        channels.insert(0, {
            "id": CHANNEL_ID,
            "name": "کانال اصلی",
            "format": "full",
            "signature": ""
        })
        existing_ids.add(str(CHANNEL_ID))

    # Default Channel 2 (Short format: only states who played against whom)
    if CHANNEL_2_ID and CHANNEL_2_ID not in existing_ids:
        channels.append({
            "id": CHANNEL_2_ID,
            "name": "کانال دوم",
            "format": "short",
            "signature": CHANNEL_2_SIGNATURE
        })
        existing_ids.add(str(CHANNEL_2_ID))

    valid = [c for c in channels if c.get("id") and "-100xxxxxxxxxx" not in str(c.get("id"))]
    return valid


def save_channels(channels):
    """Saves channels configuration to channels.json."""
    with open(CHANNELS_FILE, "w", encoding="utf-8") as f:
        json.dump(channels, f, ensure_ascii=False, indent=2)


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
    channels = load_channels()
    if not channels and (not CHANNEL_ID or "-100xxxxxxxxxx" in CHANNEL_ID):
        print("[!] Error: No destination channels configured. Please set CHANNEL_ID or configure channels.json.")
        return False
    return True


def normalize_text(text):
    """Normalizes Persian/Arabic characters, decomposed forms, and spaces for matching."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("ي", "ی").replace("ك", "ک").replace("‌", " ")
    text = text.replace("آ", "آ").replace("ئ", "ئ").replace("یٔ", "ئ")
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
    """Loads monitored team names from teams.txt and optional TEAMS environment variable."""
    teams = []
    if os.path.exists(TEAMS_FILE):
        with open(TEAMS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                cleaned = line.strip()
                if cleaned and not cleaned.startswith("#"):
                    teams.append(normalize_text(cleaned))
    else:
        default_teams = ["پرسپولیس", "استقلال", "رئال مادرید", "بارسلونا"]
        with open(TEAMS_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(default_teams))
        teams = [normalize_text(t) for t in default_teams]

    # Support adding or overriding teams via environment variable TEAMS (comma or newline separated)
    env_teams = os.getenv("TEAMS")
    if env_teams:
        for t in env_teams.replace("\n", ",").split(","):
            cleaned = t.strip()
            if cleaned and not cleaned.startswith("#"):
                teams.append(normalize_text(cleaned))

    # Deduplicate while preserving order
    return list(dict.fromkeys(teams))


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

def format_spoiler_title(title):
    """
    Detects match score from title and wraps it in Telegram <tg-spoiler> tags.
    Extracts team names to format like: 'Team A 🆚 Team B'
    """
    clean = re.sub(r"^خلاصه\s+بازی\s*", "", title).strip()

    # Pattern 1: Team1 Score1 - Team2 Score2 (e.g., 'رئال اسپانیول 1 - رئال مادرید 2')
    m1 = re.match(r"^(.*?)\s+(\d+)\s*[-–—]\s*(.*?)\s+(\d+)$", clean)
    if m1:
        t1, s1, t2, s2 = m1.group(1).strip(), m1.group(2), m1.group(3).strip(), m1.group(4)
        return (
            f"⚽️ <b>خلاصه بازی {t1} 🆚 {t2}</b>\n"
            f"📊 نتیجه نهایی: <tg-spoiler><b>{t1} {s1} - {s2} {t2}</b></tg-spoiler>"
        )

    # Pattern 2: Team1 Score1 - Score2 Team2 (e.g., 'بارسلونا 3 - 0 الچه')
    m2 = re.match(r"^(.*?)\s+(\d+)\s*[-–—]\s*(\d+)\s+(.*)$", clean)
    if m2:
        t1, s1, s2, t2 = m2.group(1).strip(), m2.group(2), m2.group(3), m2.group(4).strip()
        return (
            f"⚽️ <b>خلاصه بازی {t1} 🆚 {t2}</b>\n"
            f"📊 نتیجه نهایی: <tg-spoiler><b>{t1} {s1} - {s2} {t2}</b></tg-spoiler>"
        )

    # Fallback: Wrap any standalone score (e.g., '1 - 0') in spoiler
    if re.search(r"\d+\s*[-–—]\s*\d+", title):
        spoiler_title = re.sub(r"(\d+\s*[-–—]\s*\d+)", r"<tg-spoiler><b>\1</b></tg-spoiler>", title)
        return f"⚽️ <b>{spoiler_title}</b>"

    return f"⚽️ <b>{title}</b>"


def extract_teams_matchup(title):
    """Extracts 'Team A 🆚 Team B' from title without match score."""
    clean = re.sub(r"^خلاصه\s+بازی\s*", "", title).strip()
    m1 = re.match(r"^(.*?)\s+(\d+)\s*[-–—]\s*(.*?)\s+(\d+)$", clean)
    if m1:
        return f"⚽️ <b>خلاصه بازی {m1.group(1).strip()} 🆚 {m1.group(3).strip()}</b>"
    m2 = re.match(r"^(.*?)\s+(\d+)\s*[-–—]\s*(\d+)\s+(.*)$", clean)
    if m2:
        return f"⚽️ <b>خلاصه بازی {m2.group(1).strip()} 🆚 {m2.group(4).strip()}</b>"
    clean_score = re.sub(r"\d+\s*[-–—]\s*\d+", "", clean).strip()
    return f"⚽️ <b>خلاصه بازی {clean_score}</b>"


def format_channel_caption(title, qualities_dict, page_url, quality_label="", channel_cfg=None):
    """
    Builds customized Telegram caption based on channel format:
    - 'short': Ultra-short: only who played against whom (+ optional signature). Default for Channel 2.
    - 'full': Matchup + Spoiler score + Quality + Direct download links + Varzesh3 link (+ signature). Default for Channel 1.
    - 'no_links': Matchup + Spoiler score + Quality + Varzesh3 link (+ signature).
    - 'custom': User-defined template.
    """
    if channel_cfg is None:
        channel_cfg = {"format": "full"}

    fmt = channel_cfg.get("format", "full").lower()
    signature = channel_cfg.get("signature", "").strip()
    custom_template = channel_cfg.get("custom_template")

    if fmt == "custom" and custom_template:
        header_text = format_spoiler_title(title)
        matchup_text = extract_teams_matchup(title)
        return custom_template.replace("{header}", header_text) \
                               .replace("{matchup}", matchup_text) \
                               .replace("{title}", title) \
                               .replace("{quality}", quality_label) \
                               .replace("{signature}", signature) \
                               .replace("{page_url}", page_url).strip()

    if fmt == "short":
        # Ultra-short format: only state who played against whom!
        matchup_text = extract_teams_matchup(title)
        lines = [matchup_text]
        if signature:
            lines.append("")
            lines.append(signature)
        return "\n".join(lines).strip()

    header_text = format_spoiler_title(title)
    lines = [header_text]

    if quality_label:
        display_q = quality_label.replace("Compressed HD", "HD بهینه‌شده")
        lines.append(f"🎬 کیفیت پخش: <b>{display_q}</b>")

    lines.append("")  # spacing

    if fmt == "full" and qualities_dict:
        lines.append("📥 <b>لینک‌های دانلود مستقیم:</b>")
        for label, url in qualities_dict.items():
            clean_label = (
                label.replace("Direct Link", "کیفیت استاندارد")
                     .replace("Original", "اصلی")
                     .replace("FHD", "Full HD")
            )
            lines.append(f"▫️ <a href='{url}'>{clean_label}</a>")
        lines.append("")

    lines.append(f"🔗 <a href='{page_url}'>مشاهده در سایت ورزش سه</a>")

    if signature:
        lines.append("")
        lines.append(signature)

    return "\n".join(lines).strip()


def build_caption(title, qualities_dict, page_url, quality_label=""):
    """Builds full caption (default for Channel 1)."""
    return format_channel_caption(title, qualities_dict, page_url, quality_label, {"format": "full"})


def build_caption_channel2(title, qualities_dict, page_url, quality_label=""):
    """Builds short caption (default for Channel 2)."""
    return format_channel_caption(title, qualities_dict, page_url, quality_label, {
        "format": "short",
        "signature": CHANNEL_2_SIGNATURE
    })


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
                "-vf", r"scale=-2:min(720\,ih)",
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
    """Selects highest quality stream (HD priority), compresses if >50MB, and uploads to Telegram."""
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo"

    # Prioritize resolutions: 720p HD first, then 1080p FHD, then 480p, then 360p/Direct Link
    priority_order = ["720p", "1080p", "480p", "360p", "direct"]
    sorted_candidates = []

    for key in priority_order:
        for label, url in qualities_dict.items():
            if key in label.lower() and (label, url) not in sorted_candidates:
                sorted_candidates.append((label, url))

    # Add remaining qualities if not already included
    for label, url in qualities_dict.items():
        if (label, url) not in sorted_candidates:
            sorted_candidates.append((label, url))

    if not sorted_candidates:
        print("[-] No valid video streams found in qualities dict.")
        return fallback_send_text(title, qualities_dict, page_url)

    target_label, target_url = sorted_candidates[0]
    target_size_mb = get_stream_file_size_mb(target_url)

    print(f"[*] Prioritizing high-quality stream: [{target_label}]" + (f" ({target_size_mb:.2f} MB)" if target_size_mb else ""))

    raw_temp_file = f"raw_{int(time.time())}.mp4"
    final_upload_file = f"final_{int(time.time())}.mp4"

    try:
        # If target stream is already <= 49MB, upload directly without re-encoding
        if target_size_mb and target_size_mb <= 49.0:
            print(f"[*] Stream [{target_label}] is within Telegram limit (<= 49MB). Downloading directly...")
            with requests.get(target_url, headers=HEADERS, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(raw_temp_file, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
            upload_path = raw_temp_file
            display_label = target_label
        else:
            # File is > 49MB or size unknown. Download and compress to ~45MB
            print(f"[*] Stream [{target_label}] exceeds 49MB. Downloading to compress with FFmpeg...")
            with requests.get(target_url, headers=HEADERS, stream=True, timeout=90) as r:
                r.raise_for_status()
                with open(raw_temp_file, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)

            downloaded_size = os.path.getsize(raw_temp_file) / (1024 * 1024)
            print(f"[*] Downloaded ({downloaded_size:.2f} MB). Compressing to target ~45MB...")

            if downloaded_size <= 49.0:
                upload_path = raw_temp_file
                display_label = target_label
            else:
                success = fast_compress_video(raw_temp_file, final_upload_file, target_mb=45.0)
                if success:
                    upload_path = final_upload_file
                    display_label = f"{target_label} (HD بهینه‌شده)"
                else:
                    print("[-] Compression failed. Checking for existing <= 49MB lower-res stream...")
                    alt_url, alt_label = None, None
                    for l, u in sorted_candidates[1:]:
                        sz = get_stream_file_size_mb(u)
                        if sz and sz <= 49.0:
                            alt_url, alt_label = u, l
                            break

                    if alt_url:
                        print(f"[*] Found alternative stream [{alt_label}] <= 49MB. Downloading...")
                        with requests.get(alt_url, headers=HEADERS, stream=True, timeout=60) as r:
                            r.raise_for_status()
                            with open(raw_temp_file, "wb") as f:
                                for chunk in r.iter_content(chunk_size=1024 * 1024):
                                    if chunk:
                                        f.write(chunk)
                        upload_path = raw_temp_file
                        display_label = alt_label
                    else:
                        print("[-] No valid stream under 50MB available. Falling back to text links.")
                        return fallback_send_text(title, qualities_dict, page_url)

        upload_size = os.path.getsize(upload_path) / (1024 * 1024)

        channels = load_channels()
        if not channels:
            print("[-] No valid channels configured for upload.")
            return False

        first_channel = channels[0]
        first_caption = format_channel_caption(title, qualities_dict, page_url, display_label, first_channel)

        print(f"[*] Uploading playable video ({upload_size:.2f} MB) to {first_channel.get('name', first_channel['id'])}...")

        with open(upload_path, "rb") as video_file:
            files = {"video": ("highlight.mp4", video_file, "video/mp4")}
            data = {
                "chat_id": first_channel["id"],
                "caption": first_caption,
                "parse_mode": "HTML",
                "supports_streaming": "true"
            }
            if os.getenv("VIDEO_SPOILER", "false").lower() == "true":
                data["has_spoiler"] = "true"

            response = requests.post(api_url, data=data, files=files, timeout=180)
            res_data = response.json()

        if res_data.get("ok"):
            print(f"[✓] Uploaded successfully to {first_channel.get('name', first_channel['id'])}.")
            file_id = res_data.get("result", {}).get("video", {}).get("file_id")

            # Dispatch to all other channels using file_id!
            for ch in channels[1:]:
                ch_id = ch.get("id")
                ch_name = ch.get("name", ch_id)
                ch_fmt = ch.get("format", "short")
                try:
                    print(f"[*] Dispatching video to {ch_name} ({ch_id}) [Format: {ch_fmt}]...")
                    ch_caption = format_channel_caption(title, qualities_dict, page_url, display_label, ch)
                    ch_data = {
                        "chat_id": ch_id,
                        "caption": ch_caption,
                        "parse_mode": "HTML",
                        "supports_streaming": "true"
                    }
                    if os.getenv("VIDEO_SPOILER", "false").lower() == "true":
                        ch_data["has_spoiler"] = "true"

                    if file_id:
                        ch_data["video"] = file_id
                        res_ch = requests.post(api_url, data=ch_data, timeout=30).json()
                    else:
                        with open(upload_path, "rb") as vf2:
                            files2 = {"video": ("highlight.mp4", vf2, "video/mp4")}
                            res_ch = requests.post(api_url, data=ch_data, files=files2, timeout=180).json()

                    if res_ch.get("ok"):
                        print(f"[✓] Playable video sent successfully to {ch_name}.")
                    else:
                        print(f"[-] Error sending to {ch_name}: {res_ch.get('description')}")
                except Exception as e_ch:
                    print(f"[-] Dispatch error for {ch_name}: {e_ch}")

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
    """Sends a text message with clean download links if video upload fails to all channels."""
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    channels = load_channels()
    if not channels:
        return False

    success = False
    for i, ch in enumerate(channels):
        caption = format_channel_caption(title, qualities_dict, page_url, "", ch)
        payload = {
            "chat_id": ch["id"],
            "text": caption,
            "parse_mode": "HTML"
        }
        try:
            res = requests.post(api_url, json=payload, timeout=20)
            if res.json().get("ok"):
                if i == 0:
                    success = True
                print(f"[✓] Fallback text sent to {ch.get('name', ch['id'])}.")
        except Exception as e:
            print(f"[-] Fallback send error ({ch.get('id')}): {e}")

    return success


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


def send_telegram_admin_message(chat_id, text):
    """Sends a formatted message to admin chat."""
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        requests.post(api_url, json=payload, timeout=15)
    except Exception as e:
        print(f"[-] Error sending admin message: {e}")


def update_workflow_interval(interval_str):
    """Updates the cron schedule in bot.yaml."""
    if not os.path.exists(WORKFLOW_FILE):
        return False, "فایل ورک‌فلو (.github/workflows/bot.yaml) یافت نشد."

    cron_map = {
        "15m": "*/15 * * * *",
        "30m": "*/30 * * * *",
        "1h": "15 * * * *",
        "2h": "15 */2 * * *",
        "3h": "15 */3 * * *",
        "6h": "15 */6 * * *",
        "12h": "15 */12 * * *",
        "24h": "0 3 * * *"
    }
    interval_clean = interval_str.strip().lower()
    if interval_clean not in cron_map:
        return False, f"فرمت نامعتبر است. بازه‌های مجاز: {', '.join(cron_map.keys())}"

    new_cron = cron_map[interval_clean]
    try:
        with open(WORKFLOW_FILE, "r", encoding="utf-8") as f:
            content = f.read()

        pattern = r"(- cron:\s*['\"][^'\"]+['\"])"
        new_content, count = re.subn(pattern, f"- cron: '{new_cron}'", content, count=1)
        if count == 0:
            return False, "الگوی cron در فایل bot.yaml پیدا نشد."

        with open(WORKFLOW_FILE, "w", encoding="utf-8") as f:
            f.write(new_content)

        return True, f"زمان‌بندی اجرا به <b>{interval_clean}</b> (<code>{new_cron}</code>) تغییر یافت."
    except Exception as e:
        return False, f"خطا در ویرایش فایل ورک‌فلو: {e}"


def process_telegram_admin_commands():
    """Fetches and executes pending admin commands from Telegram."""
    if not BOT_TOKEN or "YOUR_BOT_TOKEN" in BOT_TOKEN:
        return

    config = load_config()
    last_update_id = config.get("last_update_id", 0)
    current_admin_id = get_admin_id()

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"offset": last_update_id + 1, "timeout": 2}

    try:
        res = requests.get(url, params=params, timeout=10)
        data = res.json()
        if not data.get("ok"):
            return

        updates = data.get("result", [])
        if not updates:
            return

        for update in updates:
            update_id = update["update_id"]
            if update_id > last_update_id:
                last_update_id = update_id

            message = update.get("message")
            if not message or "text" not in message:
                continue

            chat_id = message["chat"]["id"]
            from_id = str(message.get("from", {}).get("id", ""))
            text = message["text"].strip()

            # Handle /setadmin command
            if text.lower() == "/setadmin":
                config["admin_id"] = from_id
                save_config(config)
                current_admin_id = from_id
                send_telegram_admin_message(chat_id, f"✅ شما با آیدی عددی <code>{from_id}</code> به عنوان ادمین ثبت شدید!\n\nاکنون می‌توانید از دستور <code>/admin</code> برای مدیریت استفاده کنید.")
                continue

            # Check admin permission
            if current_admin_id and from_id != current_admin_id:
                send_telegram_admin_message(chat_id, "⛔️ شما دسترسی ادمین برای مدیریت این ربات را ندارید.")
                continue
            elif not current_admin_id:
                send_telegram_admin_message(chat_id, f"⚠️ هنوز ادمینی برای ربات ثبت نشده است.\n\nبرای ثبت خود به عنوان ادمین، دستور <code>/setadmin</code> را بفرستید.")
                continue

            # Command routing
            parts = text.split(maxsplit=2)
            cmd = parts[0].lower()

            if cmd in ["/start", "/help", "/panel", "/admin"]:
                help_text = (
                    "⚽️ <b>پنل مدیریت ربات خلاصه بازی‌ها</b>\n\n"
                    "📋 <b>مدیریت تیم‌ها:</b>\n"
                    "▫️ <code>/teams</code> : مشاهده لیست تیم‌ها\n"
                    "▫️ <code>/addteam &lt;نام&gt;</code> : افزودن تیم (مثال: <code>/addteam میلان</code>)\n"
                    "▫️ <code>/delteam &lt;نام&gt;</code> : حذف تیم\n\n"
                    "📢 <b>مدیریت کانال‌ها:</b>\n"
                    "▫️ <code>/channels</code> : لیست کانال‌های فعال و فرمت‌ها\n"
                    "▫️ <code>/addchannel &lt;chat_id&gt; [short|full|no_links] [امضا]</code>\n"
                    "▫️ <code>/delchannel &lt;chat_id&gt;</code> : حذف کانال\n"
                    "▫️ <code>/setformat &lt;chat_id&gt; &lt;short|full|no_links&gt;</code> : تغییر فرمت\n\n"
                    "⏱ <b>تنظیم زمان‌بندی (گیت‌هاب اکشنز):</b>\n"
                    "▫️ <code>/interval &lt;30m|1h|2h|3h&gt;</code> : تغییر فاصله اجرا\n\n"
                    "🚀 <b>اجرای فوری:</b>\n"
                    "▫️ <code>/check</code> : بررسی فوری و ارسال مسابقات جدید"
                )
                send_telegram_admin_message(chat_id, help_text)

            elif cmd == "/teams":
                teams = load_target_teams()
                if not teams:
                    send_telegram_admin_message(chat_id, "هیچ تیمی در لیست مانیتورینگ نیست.")
                else:
                    teams_list = "\n".join([f"{i+1}. {t}" for i, t in enumerate(teams)])
                    send_telegram_admin_message(chat_id, f"📋 <b>لیست تیم‌های تحت نظارت ({len(teams)} تیم):</b>\n\n{teams_list}")

            elif cmd == "/addteam":
                if len(parts) < 2:
                    send_telegram_admin_message(chat_id, "لطفاً نام تیم را وارد کنید:\n<code>/addteam میلان</code>")
                else:
                    team_name = text[len(cmd):].strip()
                    with open(TEAMS_FILE, "a", encoding="utf-8") as f:
                        f.write(f"\n{team_name}\n")
                    send_telegram_admin_message(chat_id, f"✅ تیم <b>{team_name}</b> به لیست اضافه شد.")

            elif cmd == "/delteam":
                if len(parts) < 2:
                    send_telegram_admin_message(chat_id, "لطفاً نام تیم برای حذف را وارد کنید:\n<code>/delteam میلان</code>")
                else:
                    del_name = normalize_text(text[len(cmd):].strip())
                    lines = []
                    found = False
                    if os.path.exists(TEAMS_FILE):
                        with open(TEAMS_FILE, "r", encoding="utf-8") as f:
                            for l in f:
                                if normalize_text(l.strip()) == del_name:
                                    found = True
                                else:
                                    lines.append(l)
                        with open(TEAMS_FILE, "w", encoding="utf-8") as f:
                            f.writelines(lines)
                    if found:
                        send_telegram_admin_message(chat_id, f"❌ تیم <b>{text[len(cmd):].strip()}</b> از لیست حذف شد.")
                    else:
                        send_telegram_admin_message(chat_id, "تیم مورد نظر در لیست پیدا نشد.")

            elif cmd == "/channels":
                channels = load_channels()
                if not channels:
                    send_telegram_admin_message(chat_id, "هیچ کانالی تنظیم نشده است.\nبا دستور <code>/addchannel</code> کانال اضافه کنید.")
                else:
                    ch_lines = []
                    for i, ch in enumerate(channels):
                        fmt = ch.get("format", "full")
                        sig = ch.get("signature") or "ندارد"
                        ch_lines.append(f"{i+1}. <b>{ch.get('name', 'کانال')}</b> (<code>{ch.get('id')}</code>)\n   ▫️ فرمت: <code>{fmt}</code> | امضا: <code>{sig}</code>")
                    send_telegram_admin_message(chat_id, f"📢 <b>کانال‌های فعال ({len(channels)} کانال):</b>\n\n" + "\n\n".join(ch_lines))

            elif cmd == "/addchannel":
                sub_parts = text.split()
                if len(sub_parts) < 2:
                    send_telegram_admin_message(chat_id, "فرمت دستور:\n<code>/addchannel &lt;chat_id&gt; [short|full|no_links] [signature]</code>\n\nمثال:\n<code>/addchannel -1001234567890 short 🆔 @MyChannel</code>")
                else:
                    new_id = sub_parts[1].strip()
                    fmt = sub_parts[2].lower() if len(sub_parts) > 2 and sub_parts[2].lower() in ["short", "full", "no_links", "custom"] else "short"
                    sig = " ".join(sub_parts[3:]) if len(sub_parts) > 3 else ""
                    channels = load_channels()
                    channels = [c for c in channels if str(c.get("id")) != new_id]
                    channels.append({
                        "id": new_id,
                        "name": f"کانال {len(channels) + 1}",
                        "format": fmt,
                        "signature": sig
                    })
                    save_channels(channels)
                    send_telegram_admin_message(chat_id, f"✅ کانال <code>{new_id}</code> با فرمت <b>{fmt}</b> به لیست کانال‌ها اضافه شد.")

            elif cmd == "/delchannel":
                if len(parts) < 2:
                    send_telegram_admin_message(chat_id, "فرمت دستور:\n<code>/delchannel &lt;chat_id&gt;</code>")
                else:
                    target_id = parts[1].strip()
                    channels = load_channels()
                    new_channels = [c for c in channels if str(c.get("id")) != target_id]
                    save_channels(new_channels)
                    send_telegram_admin_message(chat_id, f"❌ کانال <code>{target_id}</code> حذف شد.")

            elif cmd == "/setformat":
                sub_parts = text.split()
                if len(sub_parts) < 3:
                    send_telegram_admin_message(chat_id, "فرمت دستور:\n<code>/setformat &lt;chat_id&gt; &lt;short|full|no_links&gt;</code>")
                else:
                    target_id = sub_parts[1].strip()
                    new_fmt = sub_parts[2].lower()
                    channels = load_channels()
                    updated = False
                    for ch in channels:
                        if str(ch.get("id")) == target_id:
                            ch["format"] = new_fmt
                            updated = True
                    if updated:
                        save_channels(channels)
                        send_telegram_admin_message(chat_id, f"✅ فرمت کانال <code>{target_id}</code> به <b>{new_fmt}</b> تغییر یافت.")
                    else:
                        send_telegram_admin_message(chat_id, "کانال مورد نظر در لیست یافت نشد.")

            elif cmd == "/interval":
                if len(parts) < 2:
                    send_telegram_admin_message(chat_id, "فرمت دستور:\n<code>/interval &lt;30m|1h|2h|3h&gt;</code>\n\nمثال: <code>/interval 2h</code>")
                else:
                    success, msg = update_workflow_interval(parts[1].strip())
                    send_telegram_admin_message(chat_id, msg)

            elif cmd == "/check":
                send_telegram_admin_message(chat_id, "🔄 در حال بررسی سایت ورزش ۳...")
                process_varzesh3()
                send_telegram_admin_message(chat_id, "✅ بررسی ورزش ۳ به پایان رسید.")

        config["last_update_id"] = last_update_id
        save_config(config)

    except Exception as e:
        print(f"[-] Error processing telegram admin commands: {e}")


def admin_polling_worker():
    """Background worker that continuously listens for admin commands in daemon mode."""
    print("[*] Admin Telegram polling worker started.")
    while True:
        try:
            process_telegram_admin_commands()
        except Exception as e:
            print(f"[-] Admin polling error: {e}")
        time.sleep(2)


# ==================== MAIN ENTRY POINT ====================
if __name__ == "__main__":
    if not validate_config():
        exit(1)

    if os.getenv("RUN_ONCE") == "true":
        print("=== Running in Single-Execution Mode (GitHub Actions) ===")
        # Process any pending admin commands before checking matches
        process_telegram_admin_commands()
        process_varzesh3()
        # Process any commands that arrived during the run
        process_telegram_admin_commands()
    else:
        threading.Thread(target=run_health_check_server, daemon=True).start()
        threading.Thread(target=admin_polling_worker, daemon=True).start()
        print(f"[*] Health check server running on port {PORT}.")
        print("=== Varzesh3 Highlights Bot started successfully ===")
        while True:
            process_varzesh3()
            print(f"[*] Sleeping for {CHECK_INTERVAL // 60} minutes...\n")
            time.sleep(CHECK_INTERVAL)