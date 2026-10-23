import os
import re
import json
import time
import threading
import subprocess
import unicodedata
import html
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

DATA_DIR = os.getenv("DATA_DIR")
if not DATA_DIR:
    if os.path.exists("/data") and os.path.isdir("/data"):
        DATA_DIR = "/data"
    else:
        DATA_DIR = "."

TEAMS_FILE = os.path.join(DATA_DIR, os.getenv("TEAMS_FILE", "teams.txt"))
HISTORY_FILE = os.path.join(DATA_DIR, os.getenv("HISTORY_FILE", "sent_videos.json"))
CHANNELS_FILE = os.path.join(DATA_DIR, os.getenv("CHANNELS_FILE", "channels.json"))
CONFIG_FILE = os.path.join(DATA_DIR, os.getenv("CONFIG_FILE", "config.json"))
WORKFLOW_FILE = os.getenv("WORKFLOW_FILE", ".github/workflows/bot.yaml")
ADMIN_ID = (
    os.getenv("ADMIN_ID")
    or os.getenv("ADMIN_CHAT_ID")
    or os.getenv("TELEGRAM_ADMIN_ID")
    or ""
)

ADMIN_STATE = {}  # {chat_id: {"action": "...", "data": ...}}

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


def get_disabled_channel_ids():
    """Returns channel IDs intentionally removed through the admin panel."""
    cfg = load_config()
    values = cfg.get("disabled_channel_ids", [])
    if not isinstance(values, list):
        return set()
    return {str(value).strip() for value in values if str(value).strip()}


def set_channel_disabled(channel_id, disabled=True):
    """Persists channel deletion even when the ID is supplied by environment."""
    channel_id = str(channel_id).strip()
    cfg = load_config()
    disabled_ids = {
        str(value).strip()
        for value in cfg.get("disabled_channel_ids", [])
        if str(value).strip()
    }
    if disabled:
        disabled_ids.add(channel_id)
    else:
        disabled_ids.discard(channel_id)
    cfg["disabled_channel_ids"] = sorted(disabled_ids)
    save_config(cfg)


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
    disabled_ids = get_disabled_channel_ids()

    # 1. From channels.json file
    if os.path.exists(CHANNELS_FILE):
        try:
            with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, list):
                    for ch in loaded:
                        cid = str(ch.get("id", "")).strip()
                        if cid and "-100xxxxxxxxxx" not in cid and cid not in existing_ids and cid not in disabled_ids:
                            channels.append(ch)
                            existing_ids.add(cid)
        except Exception as e:
            print(f"[-] Error reading {CHANNELS_FILE}: {e}")

    # 2. Main Channel (Channel 1) from environment
    ch1_id = os.getenv("CHANNEL_ID") or os.getenv("TELEGRAM_CHANNEL_ID") or CHANNEL_ID
    if ch1_id and str(ch1_id).strip() not in existing_ids and str(ch1_id).strip() not in disabled_ids and "-100xxxxxxxxxx" not in str(ch1_id):
        channels.insert(0, {
            "id": str(ch1_id).strip(),
            "name": "کانال اصلی",
            "format": "full",
            "signature": ""
        })
        existing_ids.add(str(ch1_id).strip())

    # 3. Channel 2 from environment (default short format)
    ch2_id = os.getenv("CHANNEL_2_ID") or os.getenv("TELEGRAM_CHANNEL_2_ID") or CHANNEL_2_ID
    if ch2_id and str(ch2_id).strip() not in existing_ids and str(ch2_id).strip() not in disabled_ids:
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
                    if cid and cid not in existing_ids and cid not in disabled_ids:
                        channels.append(ch)
                        existing_ids.add(cid)
        except Exception as e:
            print(f"[-] Error parsing CHANNELS_JSON env var: {e}")

    return [c for c in channels if c.get("id") and "-100xxxxxxxxxx" not in str(c.get("id"))]


def save_channels(channels):
    """Saves channels configuration to channels.json."""
    with open(CHANNELS_FILE, "w", encoding="utf-8") as f:
        json.dump(channels, f, ensure_ascii=False, indent=2)


def find_channel(channels, channel_id):
    """Returns a channel config by Telegram ID."""
    return next(
        (ch for ch in channels if str(ch.get("id")) == str(channel_id)),
        None
    )


def get_caption_options(channel_cfg):
    """Returns normalized per-channel caption options with legacy format support."""
    fmt = str(channel_cfg.get("format", "full")).lower()
    defaults_by_format = {
        "short": {
            "show_score": False,
            "show_quality": False,
            "show_download_links": False,
            "show_source_link": False,
        },
        "no_links": {
            "show_score": True,
            "show_quality": True,
            "show_download_links": False,
            "show_source_link": True,
        },
        "full": {
            "show_score": True,
            "show_quality": True,
            "show_download_links": True,
            "show_source_link": True,
        },
    }
    options = defaults_by_format.get(fmt, defaults_by_format["full"]).copy()
    saved_options = channel_cfg.get("caption_options")
    if isinstance(saved_options, dict):
        for key in options:
            if key in saved_options:
                options[key] = bool(saved_options[key])
    return options


def set_channel_caption_preset(channel_cfg, preset):
    """Applies a named caption preset and clears granular overrides."""
    channel_cfg["format"] = preset
    channel_cfg.pop("caption_options", None)


def caption_style_label(channel_cfg):
    """Human-readable caption style for the admin panel."""
    if isinstance(channel_cfg.get("caption_options"), dict):
        return "شخصی‌سازی‌شده"
    labels = {
        "short": "خلاصه",
        "full": "کامل",
        "no_links": "بدون لینک دانلود",
        "custom": "قالب سفارشی",
    }
    return labels.get(str(channel_cfg.get("format", "full")).lower(), "کامل")


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
    """Loads monitored team names from teams.txt and optional TARGET_TEAMS / TEAMS environment variable."""
    teams = []
    if os.path.exists(TEAMS_FILE):
        with open(TEAMS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                cleaned = line.strip()
                if cleaned and not cleaned.startswith("#"):
                    teams.append(cleaned)
    elif os.path.exists("teams.txt"):
        with open("teams.txt", "r", encoding="utf-8") as f:
            for line in f:
                cleaned = line.strip()
                if cleaned and not cleaned.startswith("#"):
                    teams.append(cleaned)
    else:
        default_teams = ["پرسپولیس", "استقلال", "سپاهان", "تراکتور", "رئال مادرید", "بارسلونا", "منچستر سیتی", "آرسنال"]
        teams = default_teams
        save_target_teams(teams)

    # Support adding or overriding teams via environment variable TARGET_TEAMS / TEAMS
    env_teams = os.getenv("TARGET_TEAMS") or os.getenv("TEAMS")
    if env_teams:
        for t in env_teams.replace("\n", ",").split(","):
            cleaned = t.strip()
            if cleaned and not cleaned.startswith("#"):
                teams.append(cleaned)

    # Deduplicate while preserving order
    seen = set()
    result = []
    for t in teams:
        norm = normalize_text(t)
        if norm and norm not in seen:
            seen.add(norm)
            result.append(t)
    return result


def save_target_teams(teams):
    """Saves monitored teams list to TEAMS_FILE."""
    with open(TEAMS_FILE, "w", encoding="utf-8") as f:
        f.write("# ==========================================\n")
        f.write("# لیست تیم‌های تحت نظارت ربات (Teams List)\n")
        f.write("# ==========================================\n\n")
        for t in teams:
            t_clean = t.strip()
            if t_clean:
                f.write(f"{t_clean}\n")


def validate_team_name(team_name):
    """Validates a single team name entered in the admin panel."""
    raw_name = str(team_name or "")
    if any(separator in raw_name for separator in ["\n", ",", "،", ";", "؛", "|"]):
        return False, "هر بار فقط نام یک تیم را بفرستید."
    cleaned = " ".join(raw_name.split())
    if len(cleaned) < 2:
        return False, "نام تیم باید حداقل ۲ نویسه داشته باشد."
    if len(cleaned) > 60:
        return False, "نام تیم باید حداکثر ۶۰ نویسه داشته باشد."
    if not any(char.isalpha() for char in cleaned):
        return False, "نام تیم باید شامل حروف باشد."
    return True, cleaned


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


def extract_football_match_teams(title):
    """Extracts both team names from a football highlight title.

    A deliberately strict ``خلاصه بازی`` prefix and a score separator are
    required. This keeps basketball/volleyball highlights and general videos
    out of the football bot before team matching even starts.
    """
    normalized_title = normalize_text(title)
    prefix_match = re.match(r"^خلاصه\s+بازی\s+(.+)$", normalized_title)
    if not prefix_match:
        return None

    match_text = prefix_match.group(1).strip()
    non_football_sports = (
        "بسکتبال", "والیبال", "هندبال", "فوتسال", "کشتی",
        "تنیس", "هاکی", "بیسبال", "راگبی", "گلبال",
    )
    if any(sport in match_text for sport in non_football_sports):
        return None

    # Team A 2 - Team B 1
    score_at_both_ends = re.match(
        r"^(.+?)\s+(\d+)\s*[-–—]\s*(.+?)\s+(\d+)$",
        match_text,
    )
    if score_at_both_ends:
        return (
            normalize_text(score_at_both_ends.group(1)),
            normalize_text(score_at_both_ends.group(3)),
        )

    # Team A 2 - 1 Team B
    score_in_middle = re.match(
        r"^(.+?)\s+(\d+)\s*[-–—]\s*(\d+)\s+(.+)$",
        match_text,
    )
    if score_in_middle:
        return (
            normalize_text(score_in_middle.group(1)),
            normalize_text(score_in_middle.group(4)),
        )

    return None


def is_matching_team(title, allowed_teams):
    """Matches a monitored team against either complete football team name."""
    match_teams = extract_football_match_teams(title)
    if not match_teams:
        return False, None

    for team in allowed_teams:
        normalized_team = normalize_text(team)
        if normalized_team and normalized_team in match_teams:
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
    if "footer" in channel_cfg:
        signature = html.escape(str(channel_cfg.get("footer", "")).strip())
    else:
        # Keep legacy signatures compatible with the Telegram HTML they may contain.
        signature = str(channel_cfg.get("signature", "")).strip()
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

    options = get_caption_options(channel_cfg)
    header_text = format_spoiler_title(title) if options["show_score"] else extract_teams_matchup(title)
    lines = [header_text]

    if options["show_quality"] and quality_label:
        display_q = quality_label.replace("Compressed HD", "HD بهینه‌شده")
        lines.append(f"🎬 کیفیت پخش: <b>{display_q}</b>")

    if options["show_download_links"] and qualities_dict:
        lines.append("")
        lines.append("📥 <b>لینک‌های دانلود مستقیم:</b>")
        for label, url in qualities_dict.items():
            clean_label = (
                label.replace("Direct Link", "کیفیت استاندارد")
                     .replace("Original", "اصلی")
                     .replace("FHD", "Full HD")
            )
            lines.append(f"▫️ <a href='{url}'>{clean_label}</a>")
    if options["show_source_link"] and page_url:
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
        [{"text": "🎛 پنل مدیریت"}, {"text": "🔄 بررسی الان"}]
    ],
    "resize_keyboard": True,
    "is_persistent": True
}


def init_persistent_storage():
    """Copies default repository files to DATA_DIR if using persistent storage."""
    if DATA_DIR != ".":
        os.makedirs(DATA_DIR, exist_ok=True)
        for fname in ["teams.txt", "channels.json", "sent_videos.json", "config.json"]:
            src = os.path.join(".", fname)
            dst = os.path.join(DATA_DIR, fname)
            if os.path.exists(src) and not os.path.exists(dst):
                try:
                    import shutil
                    shutil.copy(src, dst)
                    print(f"[*] Copied default {fname} to persistent volume: {DATA_DIR}")
                except Exception as e:
                    print(f"[-] Error copying {fname} to {DATA_DIR}: {e}")


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
    """Clean, categorized main panel."""
    return {
        "inline_keyboard": [
            [{"text": "🔄 بررسی فوری مسابقات", "callback_data": "menu:check"}],
            [
                {"text": "⚽ تیم‌ها", "callback_data": "menu:teams"},
                {"text": "📺 کانال‌ها", "callback_data": "menu:channels"}
            ],
            [{"text": "⏱ زمان‌بندی بررسی", "callback_data": "menu:interval"}]
        ]
    }


def build_interval_submenu_keyboard(current_seconds=None):
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


def build_back_keyboard(back_callback="menu:home"):
    return {
        "inline_keyboard": [
            [{"text": "🔙 بازگشت", "callback_data": back_callback}]
        ]
    }


def build_teams_keyboard():
    teams = load_target_teams()
    keyboard = [[{"text": "➕ افزودن تیم", "callback_data": "teams:add"}]]
    if teams:
        keyboard.append([{"text": "🗑 انتخاب تیم برای حذف", "callback_data": "teams:del_menu"}])
    keyboard.append([{"text": "🏠 پنل اصلی", "callback_data": "menu:home"}])
    return {
        "inline_keyboard": keyboard
    }


def build_teams_delete_keyboard():
    teams = load_target_teams()
    keyboard = []
    row = []
    for i, t in enumerate(teams):
        row.append({"text": f"⚽ {t}", "callback_data": f"team_del_confirm:{i}"})
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([{"text": "🔙 انصراف و بازگشت", "callback_data": "menu:teams"}])
    return {"inline_keyboard": keyboard}


def build_team_delete_confirm_keyboard(team_index):
    return {
        "inline_keyboard": [
            [{"text": "✅ بله، حذف شود", "callback_data": f"team_del_do:{team_index}"}],
            [{"text": "↩️ نه، برگرد", "callback_data": "teams:del_menu"}],
        ]
    }


def build_channels_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "➕ افزودن کانال", "callback_data": "chan:add"}],
            [{"text": "🎨 تنظیم ظاهر پیام‌ها", "callback_data": "chan:settings"}],
            [
                {"text": "🗑 حذف کانال", "callback_data": "chan:del_menu"},
                {"text": "🏠 پنل اصلی", "callback_data": "menu:home"}
            ]
        ]
    }


def build_channels_select_for_format_keyboard():
    channels = load_channels()
    keyboard = []
    for index, ch in enumerate(channels):
        cid = str(ch.get("id"))
        cname = ch.get("name") or cid
        style = caption_style_label(ch)
        keyboard.append([{"text": f"📺 {cname} · {style}", "callback_data": f"chan_cfg:{index}"}])
    keyboard.append([{"text": "🔙 بازگشت به مدیریت کانال‌ها", "callback_data": "menu:channels"}])
    return {"inline_keyboard": keyboard}


def build_channels_format_pick_keyboard(cid):
    return {
        "inline_keyboard": [
            [{"text": "⚡️ خیلی کوتاه (فقط نام دو تیم)", "callback_data": f"chan_setfmt:{cid}:short"}],
            [{"text": "📄 کامل با کیفیت‌ها و لینک دانلود", "callback_data": f"chan_setfmt:{cid}:full"}],
            [{"text": "🔕 کامل بدون لینک دانلود", "callback_data": f"chan_setfmt:{cid}:no_links"}],
            [{"text": "🔙 بازگشت به لیست کانال‌ها", "callback_data": "menu:channels"}]
        ]
    }


def build_channel_settings_keyboard(channel_index, channel_cfg):
    options = get_caption_options(channel_cfg)

    def toggle_label(key, label):
        return f"{'✅' if options[key] else '▫️'} {label}"

    footer = channel_cfg.get("footer", channel_cfg.get("signature", "")).strip()
    footer_row = [
        {"text": "✍️ متن دلخواه پایین پیام", "callback_data": f"chan_footer:{channel_index}"}
    ]
    if footer:
        footer_row.append({"text": "🧹 پاک‌کردن", "callback_data": f"chan_footer_clear:{channel_index}"})

    return {
        "inline_keyboard": [
            [
                {"text": "ساده", "callback_data": f"chan_preset:{channel_index}:short"},
                {"text": "بدون دانلود", "callback_data": f"chan_preset:{channel_index}:no_links"},
                {"text": "کامل", "callback_data": f"chan_preset:{channel_index}:full"},
            ],
            [
                {"text": toggle_label("show_score", "نتیجه"), "callback_data": f"chan_toggle:{channel_index}:score"},
                {"text": toggle_label("show_quality", "کیفیت"), "callback_data": f"chan_toggle:{channel_index}:quality"},
            ],
            [
                {"text": toggle_label("show_download_links", "لینک دانلود"), "callback_data": f"chan_toggle:{channel_index}:downloads"},
                {"text": toggle_label("show_source_link", "لینک منبع"), "callback_data": f"chan_toggle:{channel_index}:source"},
            ],
            footer_row,
            [{"text": "👁 پیش‌نمایش پیام", "callback_data": f"chan_preview:{channel_index}"}],
            [{"text": "🔙 فهرست کانال‌ها", "callback_data": "chan:settings"}],
        ]
    }


def get_channel_settings_text(channel_index, channel_cfg):
    options = get_caption_options(channel_cfg)
    footer = channel_cfg.get("footer", channel_cfg.get("signature", "")).strip()
    footer_status = html.escape(footer) if footer else "تنظیم نشده"
    enabled_count = sum(bool(value) for value in options.values())
    return (
        f"🎨 <b>ظاهر پیام کانال {html.escape(str(channel_cfg.get('name') or channel_cfg.get('id')))}</b>\n\n"
        f"سبک فعلی: <b>{caption_style_label(channel_cfg)}</b>\n"
        f"اجزای فعال: <code>{enabled_count} از ۴</code>\n"
        f"متن پایین پیام: <code>{footer_status}</code>\n\n"
        "هر بخش را جداگانه روشن یا خاموش کنید؛ یا یکی از سه قالب آماده را بزنید."
    )


def build_channels_delete_keyboard():
    channels = load_channels()
    keyboard = []
    for channel_index, ch in enumerate(channels):
        cid = str(ch.get("id"))
        cname = ch.get("name") or cid
        keyboard.append([{"text": f"📺 {cname}", "callback_data": f"chan_del_confirm:{channel_index}"}])
    keyboard.append([{"text": "🔙 انصراف و بازگشت", "callback_data": "menu:channels"}])
    return {"inline_keyboard": keyboard}


def build_channel_delete_confirm_keyboard(channel_index):
    return {
        "inline_keyboard": [
            [{"text": "✅ بله، کانال حذف شود", "callback_data": f"chan_del_do:{channel_index}"}],
            [{"text": "↩️ نه، برگرد", "callback_data": "chan:del_menu"}],
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
        return (
            "⚽ <b>تیم‌های تحت نظارت</b>\n\n"
            "هنوز تیمی اضافه نشده است.\n"
            "برای شروع، دکمهٔ «➕ افزودن تیم» را بزنید."
        )
    teams_list = "\n".join([f"{i+1}.  <b>{html.escape(t)}</b>" for i, t in enumerate(teams)])
    return (
        f"⚽ <b>تیم‌های تحت نظارت</b> · {len(teams)} تیم\n\n"
        f"{teams_list}\n\n"
        "نام تیم‌ها به‌صورت کامل تطبیق داده می‌شود؛ مثلاً «استقلال» با «استقلال خوزستان» یکی نیست."
    )


def get_channels_text():
    channels = load_channels()
    if not channels:
        return (
            "📺 <b>کانال‌های مقصد</b>\n\n"
            "هنوز کانالی متصل نشده است.\n"
            "ربات را در کانال ادمین کنید و سپس «➕ افزودن کانال» را بزنید."
        )
    ch_lines = []
    for i, ch in enumerate(channels):
        footer = ch.get("footer", ch.get("signature", "")).strip()
        footer_status = "دارد" if footer else "ندارد"
        ch_lines.append(
            f"{i+1}. <b>{html.escape(str(ch.get('name', 'کانال')))}</b> "
            f"(<code>{html.escape(str(ch.get('id')))}</code>)\n"
            f"   🎨 {caption_style_label(ch)} · ✍️ متن دلخواه: {footer_status}"
        )
    return (
        f"📺 <b>کانال‌های مقصد</b> · {len(channels)} کانال\n\n"
        + "\n\n".join(ch_lines)
        + "\n\nاز بخش «تنظیم ظاهر پیام‌ها» می‌توانید محتوای هر کانال را جداگانه بچینید."
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
    """Fetches and executes pending admin commands, text inputs, and button clicks from Telegram."""
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

            # 1. Handle Inline Button Clicks (callback_query)
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

                # Main Home
                if cb_data == "menu:home":
                    answer_callback_query(cb_id)
                    ADMIN_STATE.pop(cb_chat_id, None)
                    if cb_msg_id:
                        edit_telegram_admin_message(
                            cb_chat_id, cb_msg_id,
                            get_dashboard_text(),
                            reply_markup=build_main_dashboard_keyboard()
                        )

                # Check Now
                elif cb_data == "menu:check":
                    answer_callback_query(cb_id, text="🚀 بررسی مسابقات ورزش ۳ آغاز شد...")
                    send_telegram_admin_message(cb_chat_id, "🔄 در حال بررسی لحظه‌ای سایت ورزش ۳...")
                    process_varzesh3()
                    send_telegram_admin_message(cb_chat_id, "✅ بررسی ورزش ۳ به پایان رسید.", reply_markup=MAIN_REPLY_KEYBOARD)

                # Interval Submenu
                elif cb_data == "menu:interval":
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

                # --- TEAMS CRUD ---
                elif cb_data == "menu:teams":
                    answer_callback_query(cb_id)
                    ADMIN_STATE.pop(cb_chat_id, None)
                    if cb_msg_id:
                        edit_telegram_admin_message(
                            cb_chat_id, cb_msg_id,
                            get_teams_text(),
                            reply_markup=build_teams_keyboard()
                        )

                elif cb_data == "teams:add":
                    answer_callback_query(cb_id)
                    ADMIN_STATE[cb_chat_id] = {"action": "wait_team_name"}
                    if cb_msg_id:
                        edit_telegram_admin_message(
                            cb_chat_id, cb_msg_id,
                            "➕ <b>افزودن تیم</b>\n\n"
                            "نام کامل تیم را در یک پیام بفرستید.\n\n"
                            "مثال درست: <code>استقلال خوزستان</code>\n"
                            "مثال درست: <code>آ اس رم</code>\n\n"
                            "ربات نام را دقیق ثبت می‌کند تا تیم‌های مشابه با هم اشتباه نشوند.",
                            reply_markup=build_back_keyboard("menu:teams")
                        )

                elif cb_data == "teams:del_menu":
                    answer_callback_query(cb_id)
                    if cb_msg_id:
                        edit_telegram_admin_message(
                            cb_chat_id, cb_msg_id,
                            "🗑 <b>حذف تیم</b>\n\nتیمی را که می‌خواهید حذف شود انتخاب کنید:",
                            reply_markup=build_teams_delete_keyboard()
                        )

                elif cb_data.startswith("team_del_confirm:"):
                    try:
                        idx = int(cb_data.rsplit(":", 1)[1])
                        teams = load_target_teams()
                        if 0 <= idx < len(teams):
                            team_name = teams[idx]
                            answer_callback_query(cb_id)
                            if cb_msg_id:
                                edit_telegram_admin_message(
                                    cb_chat_id, cb_msg_id,
                                    f"⚠️ تیم <b>{html.escape(team_name)}</b> از فهرست نظارت حذف شود؟",
                                    reply_markup=build_team_delete_confirm_keyboard(idx)
                                )
                    except Exception as e:
                        print(f"[-] Error selecting team for deletion: {e}")

                elif cb_data.startswith("team_del_do:"):
                    try:
                        idx = int(cb_data.rsplit(":", 1)[1])
                        teams = load_target_teams()
                        if not 0 <= idx < len(teams):
                            raise IndexError("team no longer exists")
                        deleted_name = teams.pop(idx)
                        save_target_teams(teams)
                        answer_callback_query(cb_id, text=f"✅ {deleted_name} حذف شد.")
                        if cb_msg_id:
                            edit_telegram_admin_message(
                                cb_chat_id, cb_msg_id,
                                get_teams_text(),
                                reply_markup=build_teams_keyboard()
                            )
                    except (ValueError, IndexError):
                        answer_callback_query(cb_id, text="این تیم دیگر در فهرست نیست.", show_alert=True)

                # --- CHANNELS CRUD ---
                elif cb_data == "menu:channels":
                    answer_callback_query(cb_id)
                    ADMIN_STATE.pop(cb_chat_id, None)
                    if cb_msg_id:
                        edit_telegram_admin_message(
                            cb_chat_id, cb_msg_id,
                            get_channels_text(),
                            reply_markup=build_channels_keyboard()
                        )

                elif cb_data == "chan:add":
                    answer_callback_query(cb_id)
                    ADMIN_STATE[cb_chat_id] = {"action": "wait_channel_input"}
                    if cb_msg_id:
                        edit_telegram_admin_message(
                            cb_chat_id, cb_msg_id,
                            "➕ <b>افزودن کانال</b> · مرحله ۱ از ۲\n\n"
                            "یک پیام از کانال را به این چت فوروارد کنید. اگر فوروارد ممکن نیست، آیدی عددی کانال را بفرستید؛ مثل:\n"
                            "<code>-1001234567890</code>\n\n"
                            "پیش‌نیاز: ربات باید در کانال، دسترسی ارسال پیام داشته باشد.",
                            reply_markup=build_back_keyboard("menu:channels")
                        )

                elif cb_data.startswith("chan_new_fmt:"):
                    fmt = cb_data.split(":")[1]
                    st = ADMIN_STATE.get(cb_chat_id, {})
                    cid = st.get("id")
                    title = st.get("title", f"کانال {cid}")
                    if cid:
                        channels = load_channels()
                        channels = [c for c in channels if str(c.get("id")) != str(cid)]
                        channel_cfg = {
                            "id": str(cid),
                            "name": title,
                            "format": fmt,
                            "signature": ""
                        }
                        channels.append(channel_cfg)
                        save_channels(channels)
                        set_channel_disabled(cid, False)
                        ADMIN_STATE.pop(cb_chat_id, None)
                        answer_callback_query(cb_id, text=f"✅ کانال {title} اضافه شد.")
                        if cb_msg_id:
                            channel_index = len(channels) - 1
                            edit_telegram_admin_message(
                                cb_chat_id, cb_msg_id,
                                f"✅ کانال <b>{html.escape(str(title))}</b> اضافه شد.\n\n"
                                + get_channel_settings_text(channel_index, channel_cfg),
                                reply_markup=build_channel_settings_keyboard(channel_index, channel_cfg)
                            )

                elif cb_data in ["chan:settings", "chan:fmt_menu"]:
                    answer_callback_query(cb_id)
                    ADMIN_STATE.pop(cb_chat_id, None)
                    if cb_msg_id:
                        edit_telegram_admin_message(
                            cb_chat_id, cb_msg_id,
                            "🎨 <b>تنظیم ظاهر پیام‌ها</b>\n\n"
                            "برای هر کانال می‌توانید نتیجه، کیفیت، لینک‌ها و متن پایین پیام را جداگانه تنظیم کنید.\n\n"
                            "یک کانال را انتخاب کنید:",
                            reply_markup=build_channels_select_for_format_keyboard()
                        )

                elif cb_data.startswith("chan_cfg:"):
                    try:
                        channel_index = int(cb_data.split(":")[1])
                        channels = load_channels()
                        channel_cfg = channels[channel_index]
                    except (ValueError, IndexError):
                        answer_callback_query(cb_id, text="این کانال دیگر وجود ندارد.", show_alert=True)
                        continue

                    ADMIN_STATE.pop(cb_chat_id, None)
                    answer_callback_query(cb_id)
                    if cb_msg_id:
                        edit_telegram_admin_message(
                            cb_chat_id, cb_msg_id,
                            get_channel_settings_text(channel_index, channel_cfg),
                            reply_markup=build_channel_settings_keyboard(channel_index, channel_cfg)
                        )

                elif cb_data.startswith("chan_preset:"):
                    try:
                        _, index_text, preset = cb_data.split(":", 2)
                        channel_index = int(index_text)
                        if preset not in ["short", "no_links", "full"]:
                            raise ValueError("invalid preset")
                        channels = load_channels()
                        channel_cfg = channels[channel_index]
                        set_channel_caption_preset(channel_cfg, preset)
                        save_channels(channels)
                    except (ValueError, IndexError):
                        answer_callback_query(cb_id, text="تنظیمات نامعتبر است.", show_alert=True)
                        continue

                    answer_callback_query(cb_id, text="✅ قالب پیام تغییر کرد.")
                    if cb_msg_id:
                        edit_telegram_admin_message(
                            cb_chat_id, cb_msg_id,
                            get_channel_settings_text(channel_index, channel_cfg),
                            reply_markup=build_channel_settings_keyboard(channel_index, channel_cfg)
                        )

                elif cb_data.startswith("chan_toggle:"):
                    option_keys = {
                        "score": "show_score",
                        "quality": "show_quality",
                        "downloads": "show_download_links",
                        "source": "show_source_link",
                    }
                    try:
                        _, index_text, option_name = cb_data.split(":", 2)
                        channel_index = int(index_text)
                        option_key = option_keys[option_name]
                        channels = load_channels()
                        channel_cfg = channels[channel_index]
                        options = get_caption_options(channel_cfg)
                        options[option_key] = not options[option_key]
                        channel_cfg["caption_options"] = options
                        save_channels(channels)
                    except (ValueError, IndexError, KeyError):
                        answer_callback_query(cb_id, text="تنظیمات نامعتبر است.", show_alert=True)
                        continue

                    answer_callback_query(cb_id, text="✅ تنظیم ذخیره شد.")
                    if cb_msg_id:
                        edit_telegram_admin_message(
                            cb_chat_id, cb_msg_id,
                            get_channel_settings_text(channel_index, channel_cfg),
                            reply_markup=build_channel_settings_keyboard(channel_index, channel_cfg)
                        )

                elif cb_data.startswith("chan_footer:"):
                    try:
                        channel_index = int(cb_data.split(":")[1])
                        channels = load_channels()
                        channel_cfg = channels[channel_index]
                    except (ValueError, IndexError):
                        answer_callback_query(cb_id, text="این کانال دیگر وجود ندارد.", show_alert=True)
                        continue

                    ADMIN_STATE[cb_chat_id] = {
                        "action": "wait_channel_footer",
                        "channel_id": str(channel_cfg.get("id")),
                    }
                    answer_callback_query(cb_id)
                    if cb_msg_id:
                        edit_telegram_admin_message(
                            cb_chat_id, cb_msg_id,
                            "✍️ <b>متن دلخواه پایین پیام</b>\n\n"
                            "متنی را که باید انتهای پیام‌های این کانال نمایش داده شود بفرستید. "
                            "می‌توانید چند خط، ایموجی، آیدی کانال یا هشتگ بنویسید.\n\n"
                            "حداکثر: <code>۵۰۰ نویسه</code>",
                            reply_markup=build_back_keyboard(f"chan_cfg:{channel_index}")
                        )

                elif cb_data.startswith("chan_footer_clear:"):
                    try:
                        channel_index = int(cb_data.split(":")[1])
                        channels = load_channels()
                        channel_cfg = channels[channel_index]
                        channel_cfg["footer"] = ""
                        channel_cfg["signature"] = ""
                        save_channels(channels)
                    except (ValueError, IndexError):
                        answer_callback_query(cb_id, text="این کانال دیگر وجود ندارد.", show_alert=True)
                        continue

                    answer_callback_query(cb_id, text="🧹 متن پایین پیام پاک شد.")
                    if cb_msg_id:
                        edit_telegram_admin_message(
                            cb_chat_id, cb_msg_id,
                            get_channel_settings_text(channel_index, channel_cfg),
                            reply_markup=build_channel_settings_keyboard(channel_index, channel_cfg)
                        )

                elif cb_data.startswith("chan_preview:"):
                    try:
                        channel_index = int(cb_data.split(":")[1])
                        channels = load_channels()
                        channel_cfg = channels[channel_index]
                    except (ValueError, IndexError):
                        answer_callback_query(cb_id, text="این کانال دیگر وجود ندارد.", show_alert=True)
                        continue

                    preview = format_channel_caption(
                        "خلاصه بازی استقلال ۲ - ۱ پرسپولیس",
                        {
                            "720p": "https://example.com/video-720.mp4",
                            "480p": "https://example.com/video-480.mp4",
                        },
                        "https://video.varzesh3.com/sample",
                        "720p",
                        channel_cfg,
                    )
                    answer_callback_query(cb_id, text="پیش‌نمایش ساخته شد.")
                    send_telegram_admin_message(
                        cb_chat_id,
                        f"👁 <b>پیش‌نمایش پیام</b>\n\n{preview}",
                        reply_markup=build_back_keyboard(f"chan_cfg:{channel_index}")
                    )

                elif cb_data.startswith("chan_choose_fmt:"):
                    cid = cb_data.split(":")[1]
                    answer_callback_query(cb_id)
                    if cb_msg_id:
                        edit_telegram_admin_message(
                            cb_chat_id, cb_msg_id,
                            f"✏️ <b>انتخاب فرمت برای کانال <code>{cid}</code>:</b>\n\nفرمت مورد نظر خود را انتخاب کنید:",
                            reply_markup=build_channels_format_pick_keyboard(cid)
                        )

                elif cb_data.startswith("chan_setfmt:"):
                    parts = cb_data.split(":")
                    cid = parts[1]
                    fmt = parts[2]
                    channels = load_channels()
                    for ch in channels:
                        if str(ch.get("id")) == str(cid):
                            ch["format"] = fmt
                    save_channels(channels)
                    answer_callback_query(cb_id, text=f"✅ فرمت به {fmt} تغییر یافت.")
                    if cb_msg_id:
                        edit_telegram_admin_message(
                            cb_chat_id, cb_msg_id,
                            get_channels_text(),
                            reply_markup=build_channels_keyboard()
                        )

                elif cb_data == "chan:del_menu":
                    answer_callback_query(cb_id)
                    if cb_msg_id:
                        edit_telegram_admin_message(
                            cb_chat_id, cb_msg_id,
                            "🗑 <b>حذف کانال</b>\n\nکانال مورد نظر را انتخاب کنید. قبل از حذف از شما تأیید می‌گیرم:",
                            reply_markup=build_channels_delete_keyboard()
                        )

                elif cb_data.startswith("chan_del_confirm:"):
                    try:
                        channel_index = int(cb_data.rsplit(":", 1)[1])
                        channels = load_channels()
                        channel_cfg = channels[channel_index]
                    except (ValueError, IndexError):
                        answer_callback_query(cb_id, text="این کانال دیگر وجود ندارد.", show_alert=True)
                        continue

                    answer_callback_query(cb_id)
                    if cb_msg_id:
                        edit_telegram_admin_message(
                            cb_chat_id, cb_msg_id,
                            f"⚠️ کانال <b>{html.escape(str(channel_cfg.get('name') or channel_cfg.get('id')))}</b> حذف شود؟\n\n"
                            "تنظیمات اختصاصی پیام این کانال نیز پاک می‌شود.",
                            reply_markup=build_channel_delete_confirm_keyboard(channel_index)
                        )

                elif cb_data.startswith("chan_del_do:"):
                    try:
                        channel_index = int(cb_data.rsplit(":", 1)[1])
                        channels = load_channels()
                        if not 0 <= channel_index < len(channels):
                            raise IndexError("channel no longer exists")
                        deleted_channel = channels.pop(channel_index)
                        save_channels(channels)
                        set_channel_disabled(deleted_channel.get("id"), True)
                    except (ValueError, IndexError):
                        answer_callback_query(cb_id, text="این کانال دیگر وجود ندارد.", show_alert=True)
                        continue

                    answer_callback_query(cb_id, text="✅ کانال حذف شد.")
                    if cb_msg_id:
                        edit_telegram_admin_message(
                            cb_chat_id, cb_msg_id,
                            get_channels_text(),
                            reply_markup=build_channels_keyboard()
                        )

                continue

            # 2. Handle Text Messages and Forwards
            message = update.get("message")
            if not message:
                continue

            chat_id = message["chat"]["id"]
            from_id = str(message.get("from", {}).get("id", ""))
            text = (message.get("text") or "").strip()

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

            # Check if admin is in interactive input state
            user_state = ADMIN_STATE.get(chat_id, {})
            current_action = user_state.get("action")

            if current_action == "wait_channel_footer":
                if not text:
                    send_telegram_admin_message(
                        chat_id,
                        "لطفاً متن پایین پیام را به‌صورت یک پیام متنی بفرستید.",
                        reply_markup=build_back_keyboard("chan:settings")
                    )
                    continue

                if len(text) > 500:
                    send_telegram_admin_message(
                        chat_id,
                        f"⚠️ متن شما {len(text)} نویسه است. لطفاً آن را به حداکثر ۵۰۰ نویسه کوتاه کنید.",
                        reply_markup=build_back_keyboard("chan:settings")
                    )
                    continue

                channels = load_channels()
                channel_cfg = find_channel(channels, user_state.get("channel_id"))
                if channel_cfg is None:
                    ADMIN_STATE.pop(chat_id, None)
                    send_telegram_admin_message(
                        chat_id,
                        "⚠️ کانال مورد نظر پیدا نشد؛ شاید حذف شده باشد.",
                        reply_markup=build_channels_keyboard()
                    )
                    continue

                channel_cfg["footer"] = text
                channel_cfg["signature"] = ""
                save_channels(channels)
                ADMIN_STATE.pop(chat_id, None)
                channel_index = channels.index(channel_cfg)
                send_telegram_admin_message(
                    chat_id,
                    "✅ متن دلخواه ذخیره شد و از این به بعد پایین پیام‌های این کانال می‌آید.\n\n"
                    + get_channel_settings_text(channel_index, channel_cfg),
                    reply_markup=build_channel_settings_keyboard(channel_index, channel_cfg)
                )
                continue

            if current_action == "wait_team_name" and text:
                is_valid, team_result = validate_team_name(text)
                if not is_valid:
                    send_telegram_admin_message(
                        chat_id,
                        f"⚠️ {team_result}\n\nیک نام معتبر بفرستید یا به فهرست تیم‌ها برگردید.",
                        reply_markup=build_back_keyboard("menu:teams")
                    )
                    continue

                team_name = team_result
                teams = load_target_teams()
                norm_existing = [normalize_text(t) for t in teams]
                if normalize_text(team_name) in norm_existing:
                    send_telegram_admin_message(
                        chat_id,
                        f"ℹ️ تیم <b>{html.escape(team_name)}</b> از قبل در فهرست وجود دارد.\n\n"
                        "می‌توانید نام تیم دیگری را بفرستید یا برگردید.",
                        reply_markup=build_back_keyboard("menu:teams")
                    )
                    continue

                teams.append(team_name)
                save_target_teams(teams)
                ADMIN_STATE.pop(chat_id, None)
                send_telegram_admin_message(
                    chat_id,
                    f"✅ تیم <b>{html.escape(team_name)}</b> اضافه شد.\n\n" + get_teams_text(),
                    reply_markup=build_teams_keyboard()
                )
                continue

            elif current_action == "wait_channel_input":
                chan_id = None
                chan_title = "کانال"

                # Check if message is forwarded from a channel
                fwd_chat = message.get("forward_from_chat")
                forward_origin = message.get("forward_origin") or {}
                if not fwd_chat and forward_origin.get("type") == "channel":
                    fwd_chat = forward_origin.get("chat")
                if fwd_chat and fwd_chat.get("type") == "channel":
                    chan_id = str(fwd_chat.get("id"))
                    chan_title = fwd_chat.get("title") or f"کانال {chan_id}"
                elif text:
                    # Clean input text
                    clean_id = text.replace(" ", "").strip()
                    if clean_id.startswith("-100") or clean_id.startswith("-") or clean_id.isdigit():
                        chan_id = clean_id
                        chan_title = f"کانال {chan_id}"

                if not chan_id:
                    send_telegram_admin_message(
                        chat_id,
                        "⚠️ آیدی یا پیام فوروارد شده نامعتبر است.\nلطفاً یک پیام از کانال فوروارد کنید یا آیدی عددی (مثل <code>-1001234567890</code>) بفرستید.",
                        reply_markup=build_back_keyboard("menu:channels")
                    )
                    continue

                channels = load_channels()
                existing_channel = find_channel(channels, chan_id)
                if existing_channel is not None:
                    ADMIN_STATE.pop(chat_id, None)
                    send_telegram_admin_message(
                        chat_id,
                        f"ℹ️ کانال <b>{html.escape(str(existing_channel.get('name') or chan_id))}</b> "
                        "از قبل متصل است.",
                        reply_markup=build_channels_keyboard()
                    )
                    continue

                ADMIN_STATE[chat_id] = {
                    "action": "wait_channel_format",
                    "id": chan_id,
                    "title": chan_title
                }

                format_pick_kb = {
                    "inline_keyboard": [
                        [
                            {"text": "ساده", "callback_data": "chan_new_fmt:short"},
                            {"text": "بدون دانلود", "callback_data": "chan_new_fmt:no_links"},
                            {"text": "کامل", "callback_data": "chan_new_fmt:full"}
                        ],
                        [{"text": "↩️ انصراف", "callback_data": "menu:channels"}]
                    ]
                }
                send_telegram_admin_message(
                    chat_id,
                    f"➕ <b>افزودن کانال</b> · مرحله ۲ از ۲\n\n"
                    f"کانال: <b>{html.escape(str(chan_title))}</b>\n"
                    f"شناسه: <code>{html.escape(str(chan_id))}</code>\n\n"
                    "قالب اولیهٔ پیام را انتخاب کنید؛ بعداً همهٔ اجزا قابل تغییرند:\n"
                    "• ساده: فقط نام دو تیم\n"
                    "• بدون دانلود: نتیجه، کیفیت و منبع\n"
                    "• کامل: همهٔ موارد و لینک‌های دانلود",
                    reply_markup=format_pick_kb
                )
                continue

            # Standard Navigation
            cmd = text.split()[0].lower() if text else ""

            if cmd in ["/start", "/panel", "/admin", "/help"] or text in ["🎛 پنل مدیریت", "🎛 پنل کنترل"]:
                ADMIN_STATE.pop(chat_id, None)
                send_telegram_admin_message(
                    chat_id,
                    get_dashboard_text(),
                    reply_markup=build_main_dashboard_keyboard()
                )

            elif cmd == "/check" or text in ["🚀 بررسی فوری", "🔄 بررسی الان"]:
                send_telegram_admin_message(chat_id, "🔄 در حال بررسی لحظه‌ای سایت ورزش ۳...")
                process_varzesh3()
                send_telegram_admin_message(chat_id, "✅ بررسی ورزش ۳ به پایان رسید.", reply_markup=MAIN_REPLY_KEYBOARD)

            else:
                send_telegram_admin_message(
                    chat_id,
                    get_dashboard_text(),
                    reply_markup=build_main_dashboard_keyboard()
                )

        # Merge with settings changed by callbacks during this polling cycle.
        # Saving the stale snapshot loaded at function start would otherwise
        # overwrite interval/channel changes made above.
        latest_config = load_config()
        latest_config["last_update_id"] = last_update_id
        save_config(latest_config)

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
    init_persistent_storage()
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
