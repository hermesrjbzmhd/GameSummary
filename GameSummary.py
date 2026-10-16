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
BOT_TOKEN = (
    os.getenv("BOT_TOKEN")
    or os.getenv("TELEGRAM_BOT_TOKEN")
    or "YOUR_BOT_TOKEN_HERE"
)
CHANNEL_ID = (
    os.getenv("CHANNEL_ID")
    or os.getenv("TELEGRAM_CHANNEL_ID")
    or "-100xxxxxxxxxx"
)
CHANNEL_2_ID = (
    os.getenv("CHANNEL_2_ID")
    or os.getenv("TELEGRAM_CHANNEL_2_ID")
    or ""
)
CHANNEL_2_SIGNATURE = os.getenv("CHANNEL_2_SIGNATURE", "")
CHANNEL_2_FORMAT = os.getenv("CHANNEL_2_FORMAT", "short")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "3600"))
PORT = int(os.getenv("PORT", 8000))
MAX_VIDEOS_PER_RUN = 5  # Process maximum 5 videos per run to prevent timeout

TEAMS_FILE = os.getenv("TEAMS_FILE", "teams.txt")
HISTORY_FILE = os.getenv("HISTORY_FILE", "sent_videos.json")
CHANNELS_FILE = os.getenv("CHANNELS_FILE", "channels.json")
CONFIG_FILE = os.getenv("CONFIG_FILE", "config.json")
WORKFLOW_FILE = os.getenv("WORKFLOW_FILE", ".github/workflows/bot.yaml")
ADMIN_ID = (
    os.getenv("ADMIN_ID")
    or os.getenv("ADMIN_CHAT_ID")
    or os.getenv("TELEGRAM_ADMIN_ID")
    or ""
)

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
    env_admin = (
        os.getenv("ADMIN_ID")
        or os.getenv("ADMIN_CHAT_ID")
        or os.getenv("TELEGRAM_ADMIN_ID")
        or ADMIN_ID
    )
    if env_admin:
        return str(env_admin).strip()
    cfg = load_config()
    return str(cfg.get("admin_id", "")).strip()


def load_channels():
    """Loads configured channels from channels.json or environment variables."""
    channels = []
    existing_ids = set()

    # 1. From channels.json file
    if os.path.exists(CHANNELS_FILE):
        try:
            with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, list):
                    for ch in loaded:
                        cid = str(ch.get("id", "")).strip()
                        if cid and "-100xxxxxxxxxx" not in cid and cid not in existing_ids:
                            channels.append(ch)
                            existing_ids.add(cid)
        except Exception as e:
            print(f"[-] Error reading {CHANNELS_FILE}: {e}")

    # 2. Main Channel (Channel 1) from environment
    ch1_id = os.getenv("CHANNEL_ID") or os.getenv("TELEGRAM_CHANNEL_ID") or CHANNEL_ID
    if ch1_id and str(ch1_id).strip() not in existing_ids and "-100xxxxxxxxxx" not in str(ch1_id):
        channels.insert(0, {
            "id": str(ch1_id).strip(),
            "name": "کانال اصلی",
            "format": "full",
            "signature": ""
        })
        existing_ids.add(str(ch1_id).strip())

    # 3. Channel 2 from environment (default short format)
    ch2_id = os.getenv("CHANNEL_2_ID") or os.getenv("TELEGRAM_CHANNEL_2_ID") or CHANNEL_2_ID
    if ch2_id and str(ch2_id).strip() not in existing_ids:
        ch2_sig = os.getenv("CHANNEL_2_SIGNATURE", CHANNEL_2_SIGNATURE)
        ch2_fmt = os.getenv("CHANNEL_2_FORMAT", CHANNEL_2_FORMAT)
        channels.append({
            "id": str(ch2_id).strip(),
            "name": "کانال دوم",
            "format": ch2_fmt,
            "signature": ch2_sig
        })
        existing_ids.add(str(ch2_id).strip())

    # 4. Custom CHANNELS_JSON env var
    env_channels = os.getenv("CHANNELS_JSON")
    if env_channels:
        try:
            parsed = json.loads(env_channels)
            if isinstance(parsed, list):
                for ch in parsed:
                    cid = str(ch.get("id", "")).strip()
                    if cid and cid not in existing_ids:
                        channels.append(ch)
                        existing_ids.add(cid)
        except Exception as e:
            print(f"[-] Error parsing CHANNELS_JSON env var: {e}")

    return [c for c in channels if c.get("id") and "-100xxxxxxxxxx" not in str(c.get("id"))]


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

    # Support adding or overriding teams via environment variable TARGET_TEAMS / TEAMS
    env_teams = os.getenv("TARGET_TEAMS") or os.getenv("TEAMS")
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


MAIN_REPLY_KEYBOARD = {
    "keyboard": [
        [{"text": "🎛 پنل مدیریت"}, {"text": "🚀 بررسی فوری"}]
    ],
    "resize_keyboard": True
}


def get_interval_display(seconds):
    mapping = {
        900: "۱۵ دقیقه",
        1800: "۳۰ دقیقه",
        3600: "۱ ساعت",
        7200: "۲ ساعت",
        10800: "۳ ساعت",
        21600: "۶ ساعت",
        43200: "۱۲ ساعت",
        86400: "۲۴ ساعت"
    }
    return mapping.get(seconds, f"{seconds // 60} دقیقه")


def build_main_dashboard_keyboard():
    """Clean, categorized main panel with only 3 rows."""
    return {
        "inline_keyboard": [
            [{"text": "⏱ تنظیم زمان‌بندی بررسی", "callback_data": "menu:interval"}],
            [{"text": "🚀 بررسی فوری مسابقات ورزش ۳", "callback_data": "menu:check"}],
            [
                {"text": "📋 مشاهده تیم‌ها", "callback_data": "menu:teams"},
                {"text": "📢 مشاهده کانال‌ها", "callback_data": "menu:channels"}
            ]
        ]
    }


def build_interval_submenu_keyboard(current_seconds=None):
    """Submenu specifically for changing check interval."""
    if current_seconds is None:
        current_seconds = CHECK_INTERVAL

    intervals = [
        [("15m", "۱۵ دقیقه", 900), ("30m", "۳۰ دقیقه", 1800)],
        [("1h", "۱ ساعت", 3600), ("2h", "۲ ساعت", 7200)],
        [("3h", "۳ ساعت", 10800), ("6h", "۶ ساعت", 21600)]
    ]

    keyboard = []
    for row in intervals:
        kb_row = []
        for code, label, sec in row:
            txt = f"✅ {label}" if sec == current_seconds else f"⏱ {label}"
            kb_row.append({"text": txt, "callback_data": f"set_interval:{code}"})
        keyboard.append(kb_row)

    keyboard.append([{"text": "🔙 بازگشت به پنل اصلی", "callback_data": "menu:home"}])
    return {"inline_keyboard": keyboard}


def build_back_keyboard():
    """Simple back button for detail views."""
    return {
        "inline_keyboard": [
            [{"text": "🔙 بازگشت به پنل اصلی", "callback_data": "menu:home"}]
        ]
    }


def get_dashboard_text():
    teams = load_target_teams()
    channels = load_channels()
    interval_str = get_interval_display(CHECK_INTERVAL)

    text = (
        "⚽️ <b>پنل مدیریت ربات خلاصه بازی‌ها</b>\n\n"
        f"⏱ <b>فاصله بررسی خودکار:</b> <code>{interval_str}</code>\n"
        f"📋 <b>تیم‌های تحت مانیتور:</b> <code>{len(teams)} تیم</code>\n"
        f"📢 <b>کانال‌های متصل:</b> <code>{len(channels)} کانال</code>\n\n"
        "👇 <i>بخش مورد نظر خود را انتخاب کنید:</i>"
    )
    return text


def get_interval_submenu_text():
    interval_str = get_interval_display(CHECK_INTERVAL)
    return (
        "⏱ <b>تنظیم زمان‌بندی بررسی ورزش ۳</b>\n\n"
        f"فاصله بررسی فعلی: <code>{interval_str}</code>\n\n"
        "👇 <i>برای تغییر فاصله بررسی، روی زمان مورد نظر کلیک کنید:</i>"
    )


def get_teams_text():
    teams = load_target_teams()
    if not teams:
        return "📋 هیچ تیمی در لیست مانیتورینگ نیست."
    teams_list = "\n".join([f"{i+1}. {t}" for i, t in enumerate(teams)])
    return (
        f"📋 <b>لیست تیم‌های تحت نظارت ({len(teams)} تیم):</b>\n\n"
        f"{teams_list}\n\n"
        "💡 <b>ثبت دائمی تیم‌ها:</b>\n"
        "▫️ فایل <code>teams.txt</code> را در گیت‌هاب ویرایش کنید.\n"
        "▫️ یا متغیر <code>TARGET_TEAMS</code> را در ریل‌وی تنظیم نمایید."
    )


def get_channels_text():
    channels = load_channels()
    if not channels:
        return (
            "📢 <b>هیچ کانالی متصل نیست!</b>\n\n"
            "💡 برای ثبت دائمی کانال، متغیر <code>TELEGRAM_CHANNEL_ID</code> را در ریل‌وی ست کنید."
        )
    ch_lines = []
    for i, ch in enumerate(channels):
        fmt = ch.get("format", "full")
        fmt_fa = "خیلی کوتاه فقط نام تیم‌ها (short)" if fmt == "short" else ("کامل با لینک (full)" if fmt == "full" else fmt)
        sig = ch.get("signature") or "ندارد"
        ch_lines.append(f"{i+1}. <b>{ch.get('name', 'کانال')}</b> (<code>{ch.get('id')}</code>)\n   ▫️ فرمت: <code>{fmt_fa}</code> | امضا: <code>{sig}</code>")
    return (
        f"📢 <b>کانال‌های فعال ({len(channels)} کانال):</b>\n\n"
        + "\n\n".join(ch_lines)
        + "\n\n💡 <b>ثبت دائمی کانال‌ها:</b>\n"
        + "برای اینکه کانال‌ها با آپدیت حذف نشوند، آن‌ها را در متغیرهای <code>TELEGRAM_CHANNEL_ID</code> و <code>CHANNEL_2_ID</code> در ریل‌وی وارد کنید."
    )


def setup_bot_commands():
    """Registers clean bot commands with Telegram for the blue Menu button."""
    if not BOT_TOKEN or "YOUR_BOT_TOKEN" in BOT_TOKEN:
        return
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/setMyCommands"
    commands = [
        {"command": "panel", "description": "🎛 پنل مدیریت و تنظیمات"},
        {"command": "check", "description": "🚀 بررسی فوری مسابقات ورزش ۳"}
    ]
    try:
        requests.post(api_url, json={"commands": commands}, timeout=10)
    except Exception as e:
        print(f"[-] Error setting bot commands: {e}")


def send_telegram_admin_message(chat_id, text, reply_markup=None):
    """Sends a formatted message to admin chat with optional keyboard."""
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        return requests.post(api_url, json=payload, timeout=15)
    except Exception as e:
        print(f"[-] Error sending admin message: {e}")
        return None


def edit_telegram_admin_message(chat_id, message_id, text, reply_markup=None):
    """Edits an existing telegram message inline."""
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(api_url, json=payload, timeout=10)
    except Exception as e:
        print(f"[-] Error editing admin message: {e}")


def answer_callback_query(cb_id, text=None, show_alert=False):
    """Answers inline button callback query to stop loading spinner and optionally show toast alert."""
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
    payload = {"callback_query_id": cb_id}
    if text:
        payload["text"] = text
    if show_alert:
        payload["show_alert"] = True
    try:
        requests.post(api_url, json=payload, timeout=5)
    except Exception:
        pass


def update_workflow_interval(interval_str):
    """Updates the check interval for Railway (in-memory) and bot.yaml (GitHub Actions)."""
    global CHECK_INTERVAL
    interval_clean = interval_str.strip().lower()

    seconds_map = {
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "2h": 7200,
        "3h": 10800,
        "6h": 21600,
        "12h": 43200,
        "24h": 86400
    }
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

    if interval_clean not in seconds_map:
        return False, f"فرمت نامعتبر است. بازه‌های مجاز: {', '.join(seconds_map.keys())}"

    new_seconds = seconds_map[interval_clean]
    CHECK_INTERVAL = new_seconds
    cfg = load_config()
    cfg["check_interval"] = new_seconds
    save_config(cfg)

    # Also update bot.yaml for GitHub Actions if file exists
    if os.path.exists(WORKFLOW_FILE):
        try:
            with open(WORKFLOW_FILE, "r", encoding="utf-8") as f:
                content = f.read()
            pattern = r"(- cron:\s*['\"][^'\"]+['\"])"
            new_content, count = re.subn(pattern, f"- cron: '{cron_map[interval_clean]}'", content, count=1)
            if count > 0:
                with open(WORKFLOW_FILE, "w", encoding="utf-8") as f:
                    f.write(new_content)
        except Exception:
            pass

    return True, f"✅ زمان‌بندی بررسی با موفقیت به <b>{get_interval_display(new_seconds)}</b> تغییر یافت."


def process_telegram_admin_commands():
    """Fetches and executes pending admin commands and button clicks from Telegram."""
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

            # 1. Handle Inline Button Click (callback_query)
            if "callback_query" in update:
                cb = update["callback_query"]
                cb_id = cb["id"]
                cb_data = cb.get("data", "")
                cb_from_id = str(cb.get("from", {}).get("id", ""))
                cb_message = cb.get("message", {})
                cb_chat_id = cb_message.get("chat", {}).get("id")
                cb_msg_id = cb_message.get("message_id")

                if current_admin_id and cb_from_id != current_admin_id:
                    answer_callback_query(cb_id, text="⛔️ دسترسی غیرمجاز!", show_alert=True)
                    continue

                if cb_data == "menu:interval":
                    answer_callback_query(cb_id)
                    if cb_msg_id:
                        edit_telegram_admin_message(
                            cb_chat_id, cb_msg_id,
                            get_interval_submenu_text(),
                            reply_markup=build_interval_submenu_keyboard(CHECK_INTERVAL)
                        )

                elif cb_data.startswith("set_interval:"):
                    interval_val = cb_data.split(":")[1]
                    success, msg = update_workflow_interval(interval_val)
                    answer_callback_query(cb_id, text=f"✅ زمان بررسی روی {get_interval_display(CHECK_INTERVAL)} تنظیم شد.")
                    if cb_msg_id:
                        edit_telegram_admin_message(
                            cb_chat_id, cb_msg_id,
                            get_interval_submenu_text(),
                            reply_markup=build_interval_submenu_keyboard(CHECK_INTERVAL)
                        )

                elif cb_data == "menu:check":
                    answer_callback_query(cb_id, text="🚀 بررسی مسابقات ورزش ۳ آغاز شد...")
                    send_telegram_admin_message(cb_chat_id, "🔄 در حال بررسی لحظه‌ای سایت ورزش ۳...")
                    process_varzesh3()
                    send_telegram_admin_message(cb_chat_id, "✅ بررسی ورزش ۳ به پایان رسید.", reply_markup=MAIN_REPLY_KEYBOARD)

                elif cb_data == "menu:teams":
                    answer_callback_query(cb_id)
                    if cb_msg_id:
                        edit_telegram_admin_message(
                            cb_chat_id, cb_msg_id,
                            get_teams_text(),
                            reply_markup=build_back_keyboard()
                        )

                elif cb_data == "menu:channels":
                    answer_callback_query(cb_id)
                    if cb_msg_id:
                        edit_telegram_admin_message(
                            cb_chat_id, cb_msg_id,
                            get_channels_text(),
                            reply_markup=build_back_keyboard()
                        )

                elif cb_data == "menu:home":
                    answer_callback_query(cb_id)
                    if cb_msg_id:
                        edit_telegram_admin_message(
                            cb_chat_id, cb_msg_id,
                            get_dashboard_text(),
                            reply_markup=build_main_dashboard_keyboard()
                        )

                continue

            # 2. Handle Text Messages / Reply Keyboard buttons
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
                send_telegram_admin_message(
                    chat_id,
                    f"✅ <b>شما به عنوان ادمین موقت ثبت شدید!</b>\n\n"
                    f"📌 <b>برای ثبت دائمی و جلوگیری از ریست شدن با آپدیت‌ها:</b>\n"
                    f"در تب <b>Variables</b> در Railway، متغیر زیر را اضافه کنید تا با هیچ آپدیتی ریست نشود:\n"
                    f"<code>ADMIN_CHAT_ID</code> = <code>{from_id}</code>",
                    reply_markup=MAIN_REPLY_KEYBOARD
                )
                send_telegram_admin_message(
                    chat_id,
                    get_dashboard_text(),
                    reply_markup=build_main_dashboard_keyboard()
                )
                continue

            # Check admin permission
            if current_admin_id and from_id != current_admin_id:
                send_telegram_admin_message(chat_id, "⛔️ شما دسترسی ادمین برای مدیریت این ربات را ندارید.")
                continue
            elif not current_admin_id:
                send_telegram_admin_message(
                    chat_id,
                    "⚠️ هنوز ادمینی برای ربات ثبت نشده است.\nبرای ثبت خود به عنوان ادمین، دستور <code>/setadmin</code> را بفرستید."
                )
                continue

            # Route by reply keyboard buttons or commands
            cmd = text.split()[0].lower() if text else ""

            if cmd in ["/start", "/panel", "/admin", "/help"] or text in ["🎛 پنل مدیریت", "🎛 پنل کنترل"]:
                send_telegram_admin_message(
                    chat_id,
                    get_dashboard_text(),
                    reply_markup=build_main_dashboard_keyboard()
                )

            elif cmd == "/check" or text == "🚀 بررسی فوری":
                send_telegram_admin_message(chat_id, "🔄 در حال بررسی لحظه‌ای سایت ورزش ۳...")
                process_varzesh3()
                send_telegram_admin_message(chat_id, "✅ بررسی ورزش ۳ به پایان رسید.", reply_markup=MAIN_REPLY_KEYBOARD)

            else:
                send_telegram_admin_message(
                    chat_id,
                    get_dashboard_text(),
                    reply_markup=build_main_dashboard_keyboard()
                )

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

    # Register bot menu commands with Telegram
    setup_bot_commands()

    if os.getenv("RUN_ONCE") == "true":
        print("=== Running in Single-Execution Mode (GitHub Actions) ===")
        process_telegram_admin_commands()
        process_varzesh3()
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