#!/usr/bin/env python3
"""
GigDownloader
=============
One downloader for YouTube, Facebook, Instagram, X (Twitter), TikTok,
Threads and Pinterest - powered by yt-dlp. Works the same on Windows, macOS, Linux and
Termux (Android).

    gig                     start (paste links one after another)
    gig <url>               start with a link already filled in
    gig -o <folder>         use another base folder for GigVideos/GigAudios
    gig --cookies FILE      use a cookies.txt for login-only posts
    gig --check             show what is installed / what is missing
    gig --version

Where files go (inside your system's Downloads folder):
    videos -> GigVideos
    audio  -> GigAudios

Login-only posts (some Instagram / X posts):
    Export your browser cookies for that site as "cookies.txt" and put it in
    the folder you run `gig` from, or in  ~/.gigdownloader/cookies.txt

Only download content you have the right to download - your own posts,
content licensed for reuse, or personal/offline use where permitted.
Respect each platform's Terms of Service and the copyright law in your
country.
"""

import argparse
import functools
from html.parser import HTMLParser
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from pathlib import Path

from gigdownloader import __version__
from gigdownloader.paths import (
    AUDIO_FOLDER,
    CONFIG_DIR,
    VIDEO_FOLDER,
    ensure_download_dirs,
    is_termux,
    termux_storage_ready,
)

try:
    import yt_dlp
except ImportError:  # pragma: no cover
    print("\n[!] yt-dlp is not installed.")
    print("    Install it with:  pip install --upgrade yt-dlp\n")
    sys.exit(1)

APP_NAME = "GigDownloader"
STATE = {"cookies": None}


# ───────────────────────── Console / colors ─────────────────────────
def _enable_ansi() -> bool:
    """Colors on every terminal that can show them (incl. Windows 10+)."""
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return False
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                return False
            return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
        except Exception:
            return False
    return True


def _setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


COLOR = _enable_ansi()


def _c(code: str) -> str:
    return f"\033[{code}m" if COLOR else ""


RESET = _c("0")
BOLD = _c("1")
GRAY = _c("38;5;244")
RED = _c("1;38;5;196")
GREEN = _c("1;38;5;82")
YELLOW = _c("1;38;5;220")
CYAN = _c("1;38;5;51")
WHITE = _c("1;38;5;255")
LAV = _c("1;38;5;183")       # logo fill
SHADOW = _c("0;38;5;99")     # logo shadow
TEAL = _c("1;38;5;80")       # subtitle

# name -> (url pattern, color)
PLATFORMS = {
    "YouTube": (
        re.compile(r"(https?://)?([\w-]+\.)?(youtube\.com|youtu\.be|youtube-nocookie\.com)/\S+", re.I),
        _c("1;38;5;196"),
    ),
    "Facebook": (
        re.compile(r"(https?://)?(www\.|m\.|web\.)?(facebook\.com|fb\.watch)/\S+", re.I),
        _c("1;38;5;33"),
    ),
    "Instagram": (
        re.compile(r"(https?://)?(www\.)?instagram\.com/\S+", re.I),
        _c("1;38;5;201"),
    ),
    "X (Twitter)": (
        re.compile(r"(https?://)?(www\.|mobile\.)?(twitter\.com|x\.com)/\S+", re.I),
        _c("1;38;5;51"),
    ),
    "TikTok": (
        re.compile(r"(https?://)?(www\.|vm\.|vt\.|m\.)?tiktok\.com/\S+", re.I),
        _c("1;38;5;204"),
    ),
    "Threads": (
        re.compile(r"(https?://)?(www\.)?threads\.(net|com)/\S+", re.I),
        _c("1;38;5;82"),
    ),
    "Pinterest": (
        re.compile(r"(https?://)?((www|m)\.)?pinterest\.com/\S+|"
                   r"(https?://)?pin\.it/\S+", re.I),
        _c("1;38;5;196"),
    ),
}

# Color for each resolution tier, best -> worst.
QUALITY_COLORS = {
    4320: _c("1;95"),   # 8K - top tier
    2160: _c("1;91"),   # 4K
    1440: _c("93"),     # 2K / QHD
    1080: _c("92"),     # Full HD
    720: _c("96"),      # HD
    480: _c("94"),      # SD
    360: GRAY,
    240: GRAY,
    144: GRAY,
}

RESOLUTION_LADDER = [
    (4320, "8K"),
    (2160, "4K"),
    (1440, "2K / QHD"),
    (1080, "Full HD"),
    (720, "HD"),
    (480, "SD"),
    (360, "Low"),
    (240, "Very Low"),
    (144, "Lowest"),
]

LOGO = [
    " ██████╗ ██╗ ██████╗ ",
    "██╔════╝ ██║██╔════╝ ",
    "██║  ███╗██║██║  ███╗",
    "██║   ██║██║██║   ██║",
    "╚██████╔╝██║╚██████╔╝",
    " ╚═════╝ ╚═╝ ╚═════╝ ",
]
LOGO_WIDTH = 21


def term_cols() -> int:
    return shutil.get_terminal_size((80, 24)).columns


def _center(text: str, cols: int) -> str:
    return " " * max(0, (cols - len(text)) // 2)


def banner() -> None:
    cols = term_cols()
    print()
    subtitle = f"Downloader  v{__version__}"
    if cols >= LOGO_WIDTH + 1:
        pad = " " * ((cols - LOGO_WIDTH) // 2)
        for line in LOGO:
            print(pad + SHADOW + line.replace("█", LAV + "█" + SHADOW) + RESET)
        print(_center(subtitle, cols) + TEAL + subtitle + RESET)
    else:
        print(f"{LAV}{APP_NAME}{RESET} {TEAL}v{__version__}{RESET}")

    tagline = "One tool, every platform  •  up to 8K"
    if cols < len(tagline) + 2:
        tagline = "Every platform • up to 8K"
    print(_center(tagline, cols) + GRAY + tagline + RESET)
    print()

    names = "  ".join(f"{color}{name}{RESET}" for name, (_, color) in PLATFORMS.items())
    print(f" {GRAY}Supports:{RESET} {names}")
    print(f" {GRAY}Runs on: {RESET}Windows · macOS · Linux · Termux")
    print()


def info(msg: str) -> None:
    print(f" {GRAY}●{RESET} {msg}")


def ok(msg: str) -> None:
    print(f" {GREEN}✔{RESET} {msg}")


def warn(msg: str) -> None:
    print(f" {YELLOW}⚠{RESET} {msg}")


def system_name() -> str:
    if is_termux():
        return "Termux"
    return {"win32": "Windows", "darwin": "macOS"}.get(sys.platform, "Linux")


# ───────────────────── JS runtime / ffmpeg / cookies ─────────────────────
def _exe_names(base: str):
    return [base + ".exe", base] if os.name == "nt" else [base]


def _find_in_dirs(base: str, dirs):
    for directory in dirs:
        for name in _exe_names(base):
            candidate = directory / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return None


def find_deno():
    return shutil.which("deno") or _find_in_dirs(
        "deno", [CONFIG_DIR / "bin", Path.home() / ".deno" / "bin"]
    )


def node_major(path: str) -> int:
    try:
        out = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        return int(out.lstrip("v").split(".")[0])
    except Exception:
        return 0


def detect_js_runtime():
    """Return (name, path, note). yt-dlp needs one to read YouTube."""
    deno = find_deno()
    if deno:
        return "deno", deno, ""
    node = shutil.which("node")
    if node:
        major = node_major(node)
        if major >= 22:
            return "node", node, ""
        return None, None, f"Node.js {major or '?'} is too old (needs 22+)"
    return None, None, "no JavaScript runtime found"


def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    return None


def find_cookies(cli_path=None):
    """cookies.txt: --cookies, $GIG_COOKIES, ./cookies.txt, ~/.gigdownloader/cookies.txt"""
    candidates = []
    if cli_path:
        candidates.append(Path(cli_path).expanduser())
    env = os.environ.get("GIG_COOKIES")
    if env:
        candidates.append(Path(env).expanduser())
    candidates += [Path.cwd() / "cookies.txt", CONFIG_DIR / "cookies.txt"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


@functools.lru_cache(maxsize=1)
def _runtime_opts() -> dict:
    """yt-dlp options that switch on the JS runtime / EJS solver scripts.

    Built with yt-dlp's own CLI parser so we never depend on internal key names.
    """
    args = []
    name, path, _ = detect_js_runtime()
    if name:
        args += ["--js-runtimes", f"{name}:{path}"]
    if importlib.util.find_spec("yt_dlp_ejs") is None:
        args += ["--remote-components", "ejs:github"]
    if not args:
        return {}
    try:
        base = yt_dlp.parse_options([]).ydl_opts
        wanted = yt_dlp.parse_options(args).ydl_opts
        return {k: v for k, v in wanted.items() if base.get(k) != v}
    except Exception:
        return {}


def base_opts(platform_name=None) -> dict:
    opts = {"quiet": True, "no_warnings": True}
    if platform_name == "YouTube":
        opts["noplaylist"] = True
        opts.update(_runtime_opts())
    if STATE["cookies"]:
        opts["cookiefile"] = str(STATE["cookies"])
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg
    return opts


# ─────────────────────────── Video helpers ───────────────────────────
def detect_platform(url: str):
    for name, (pattern, _) in PLATFORMS.items():
        if pattern.match(url):
            return name
    return None


def get_url(preset=None):
    candidate = preset
    while True:
        if not candidate:
            candidate = input(f"\n{WHITE}Paste a link:{RESET} ").strip()
        name = detect_platform(candidate)
        if name:
            if not candidate.lower().startswith("http"):
                candidate = "https://" + candidate
            return candidate, name
        print(f"{YELLOW}[!] That doesn't look like a supported link. Try again.{RESET}")
        candidate = None


def probe_video(url: str, platform_name: str) -> dict:
    """Fetch metadata + available formats without downloading anything."""
    opts = base_opts(platform_name)
    opts["skip_download"] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


class _PinterestImageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.image = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "meta":
            return
        attrs = dict(attrs)
        if attrs.get("property", "").lower() in ("og:image", "og:image:secure_url"):
            self.image = attrs.get("content") or self.image


def download_pinterest_image(url: str, videos_dir: Path):
    """Fallback for image pins when yt-dlp's Pinterest extractor has no video formats."""
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=20) as response:
        page = response.read(2_000_000).decode("utf-8", errors="replace")
    parser = _PinterestImageParser()
    parser.feed(page)
    if not parser.image:
        return None

    image_url = urljoin(url, parser.image)
    image_request = Request(image_url, headers={"User-Agent": "Mozilla/5.0", "Referer": url})
    with urlopen(image_request, timeout=30) as response:
        content_type = response.headers.get_content_type()
        if not content_type.startswith("image/"):
            return None
        extension = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(content_type, ".img")
        match = re.search(r"/pin/(\d+)", url)
        filename = f"Pinterest-{match.group(1) if match else 'pin'}{extension}"
        destination = videos_dir / filename
        destination.write_bytes(response.read())
    return destination


def available_qualities(video: dict):
    """(height, label) for each ladder step this video really has, best first."""
    formats = video.get("formats", [])
    heights_present = {f.get("height") for f in formats if f.get("height")}

    found = [(h, label) for h, label in RESOLUTION_LADDER if h in heights_present]

    # Odd heights (e.g. 2158): fall back to whatever heights exist.
    if not found and heights_present:
        found = [(h, f"{h}p") for h in sorted(heights_present, reverse=True)]
    return found


def choose_quality(qualities):
    print(f"\n{BOLD}Options:{RESET}")
    print(f"  {GREEN}0. Best available{RESET} {GRAY}(auto - highest quality found){RESET}")

    if qualities:
        for i, (height, label) in enumerate(qualities, start=1):
            color = QUALITY_COLORS.get(height, GRAY)
            tag = f" {BOLD}★ 8K{RESET}{color}" if height == 4320 else ""
            print(f"  {color}{i}. {height}p - {label}{tag}{RESET}")
        audio_index = len(qualities) + 1
    else:
        print(f"  {GRAY}   (Only a single stream was found for this link.){RESET}")
        audio_index = 1
    print(f"  {CYAN}{audio_index}. Audio only (MP3){RESET}")

    while True:
        choice = input(f"\n{WHITE}Choose an option [0-{audio_index}]:{RESET} ").strip()
        if not choice.isdigit():
            print(f"{YELLOW}[!] Enter a number.{RESET}")
            continue
        choice = int(choice)
        if choice == 0:
            return ("video", None)
        if qualities and 1 <= choice <= len(qualities):
            return ("video", qualities[choice - 1][0])
        if choice == audio_index:
            return ("audio", None)
        print(f"{YELLOW}[!] Invalid option.{RESET}")


# ─────────────────────────── Progress bar ───────────────────────────
def _fmt_bytes(n) -> str:
    if not n:
        return "?"
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _fmt_eta(seconds) -> str:
    if seconds is None:
        return "--:--"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def make_progress_hook():
    def hook(d):
        status = d.get("status")
        if status == "downloading":
            done = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            frac = min(1.0, done / total) if total else 0.0
            speed = d.get("speed")
            info_dict = d.get("info_dict") or {}
            vcodec, acodec = info_dict.get("vcodec"), info_dict.get("acodec")
            if vcodec not in (None, "none"):
                label = "Video"
            elif acodec not in (None, "none"):
                label = "Audio"
            else:
                label = "File "

            meta = f" {frac * 100:5.1f}%"
            speed_s = f"{_fmt_bytes(speed)}/s" if speed else "--"
            cols = term_cols()
            if cols >= 62:
                meta += f"  {_fmt_bytes(done)}/{_fmt_bytes(total)}  {speed_s}  ETA {_fmt_eta(d.get('eta'))}"
            elif cols >= 40:
                meta += f"  {speed_s}"
            bar_w = max(8, min(28, cols - len(label) - len(meta) - 6))
            filled = int(bar_w * frac)
            if COLOR:
                bar = f"{LAV}{'█' * filled}{GRAY}{'░' * (bar_w - filled)}{RESET}"
                line = f"\r\033[K  {TEAL}{label}{RESET} {bar}{WHITE}{meta}{RESET}"
            else:
                line = f"\r  {label} {'#' * filled}{'-' * (bar_w - filled)}{meta}"
            sys.stdout.write(line)
            sys.stdout.flush()
        elif status == "finished":
            sys.stdout.write("\r\033[K" if COLOR else "\r")
            print(f"  {GREEN}✔{RESET} stream downloaded")

    return hook


def output_template(platform_name: str, mode: str, videos_dir: Path, audios_dir: Path) -> str:
    if platform_name == "YouTube":
        name = "%(title).100s"
        tag = " [%(resolution)s]" if mode == "video" else ""
    else:
        name = "%(uploader).40s - %(title).80s_%(upload_date)s"
        tag = ""
    folder = videos_dir if mode == "video" else audios_dir
    return str(folder / f"{name}{tag}.%(ext)s")


def download(url, platform_name, mode, height, videos_dir, audios_dir, have_ffmpeg) -> Path:
    opts = base_opts(platform_name)
    opts["progress_hooks"] = [make_progress_hook()]
    opts["outtmpl"] = output_template(platform_name, mode, videos_dir, audios_dir)

    if mode == "audio":
        opts["format"] = "bestaudio/best"
        if have_ffmpeg:
            opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
        saved_in = audios_dir
    else:
        if have_ffmpeg:
            opts["format"] = (
                f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"
                if height else "bestvideo+bestaudio/best"
            )
            opts["merge_output_format"] = "mp4"
        else:
            # Without ffmpeg we can only take single-file (pre-merged) formats.
            opts["format"] = f"best[height<={height}]/best" if height else "best"
        saved_in = videos_dir

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    return saved_in


def explain_error(exc, platform_name=None) -> None:
    msg = str(exc).lower()
    print(f"\n{RED}[!] Download failed: {exc}{RESET}")
    if "login" in msg or "rate-limit" in msg or "private" in msg:
        print(
            f"{YELLOW}    This post may require being logged in. Export your browser's{RESET}\n"
            f"{YELLOW}    cookies as cookies.txt and put it in the folder you run gig from,{RESET}\n"
            f"{YELLOW}    or in {CONFIG_DIR}{RESET}"
        )
    if platform_name == "Threads":
        print(
            f"{YELLOW}    Threads needs the yt-dlp-threads plugin. If you haven't yet,{RESET}\n"
            f"{YELLOW}    run:  pip install --upgrade yt-dlp-threads{RESET}"
        )


# ───────────────────────── Commands / entry ─────────────────────────
def check(args) -> None:
    """`gig --check`: show what is installed and what is missing."""
    ok(f"System: {system_name()}  •  Python {sys.version.split()[0]}")
    ok(f"yt-dlp {yt_dlp.version.__version__}")

    name, path, note = detect_js_runtime()
    if name:
        ok(f"JavaScript runtime (YouTube): {name} ({path})")
    else:
        warn(f"JavaScript runtime (YouTube): {note} - YouTube may show fewer formats")

    if importlib.util.find_spec("yt_dlp_ejs"):
        ok("EJS solver scripts: installed")
    else:
        warn("EJS solver scripts: not installed (fetched from GitHub when needed)")

    ffmpeg = find_ffmpeg()
    if ffmpeg:
        ok(f"ffmpeg: {ffmpeg}")
    else:
        warn("ffmpeg: not found - high-quality merging and MP3 need it")

    cookies = find_cookies(args.cookies)
    info(f"Cookies: {cookies if cookies else 'none (only needed for login-only posts)'}")

    videos, audios = ensure_download_dirs(args.output)
    ok(f"Videos folder: {videos}")
    ok(f"Audios folder: {audios}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="gig",
        description=f"{APP_NAME} - YouTube, Facebook, Instagram, X, TikTok, Threads and Pinterest downloader",
    )
    parser.add_argument("url", nargs="?", help="video link (optional)")
    parser.add_argument("-o", "--output", metavar="DIR",
                        help="base folder to create GigVideos/GigAudios in")
    parser.add_argument("--cookies", metavar="FILE", help="cookies.txt for login-only posts")
    parser.add_argument("-V", "--version", action="store_true", help="show version and exit")
    parser.add_argument("--check", action="store_true", help="check installed components")
    return parser.parse_args(argv)


def run(args) -> None:
    banner()
    videos_dir, audios_dir = ensure_download_dirs(args.output)
    STATE["cookies"] = find_cookies(args.cookies)

    info(f"System: {WHITE}{system_name()}{RESET}  {GRAY}•  Python {sys.version.split()[0]}  •  yt-dlp {yt_dlp.version.__version__}{RESET}")
    info(f"Videos → {WHITE}{videos_dir}{RESET}")
    info(f"Audios → {WHITE}{audios_dir}{RESET}")
    if STATE["cookies"]:
        info(f"Using cookies: {WHITE}{STATE['cookies']}{RESET}")

    if is_termux() and not termux_storage_ready():
        warn(f"Tip: run {CYAN}termux-setup-storage{RESET} to save into your phone's Downloads folder")
    have_ffmpeg = bool(find_ffmpeg())
    if not have_ffmpeg:
        warn("ffmpeg not found - 720p and above can't be merged and MP3 is unavailable")
    info(f"{GRAY}Paste a link and press Enter. Press Ctrl+C any time to quit.{RESET}")

    js_warned = False
    preset = args.url
    while True:
        url, platform_name = get_url(preset)
        preset = None
        pcolor = PLATFORMS[platform_name][1]
        print(f"\nDetected: {pcolor}{platform_name}{RESET}")

        if platform_name == "YouTube" and not js_warned:
            name, _, note = detect_js_runtime()
            if not name:
                warn(f"{note} - YouTube may show fewer qualities (run {CYAN}gig --check{RESET})")
                js_warned = True

        print(f"{GRAY}Fetching info...{RESET}")
        try:
            video = probe_video(url, platform_name)
        except yt_dlp.utils.DownloadError as exc:
            if platform_name == "Pinterest":
                try:
                    saved_image = download_pinterest_image(url, videos_dir)
                    if saved_image:
                        print(f"\n{GREEN}[✔] Image pin saved: {saved_image}{RESET}")
                        continue
                except Exception:
                    pass
            explain_error(exc, platform_name)
            continue

        title = video.get("title") or (video.get("description") or "")[:60] or "Untitled"
        duration = video.get("duration")
        print(f"\n{BOLD}Title:{RESET} {title}")
        uploader = video.get("uploader")
        if uploader:
            print(f"{BOLD}From:{RESET} {uploader}")
        if duration:
            print(f"{BOLD}Duration:{RESET} {int(duration) // 60}m {int(duration) % 60}s")

        qualities = available_qualities(video)
        if not qualities and platform_name == "YouTube":
            print(f"{YELLOW}[!] No downloadable video formats found for this video.{RESET}")
            continue

        mode, height = choose_quality(qualities)

        print()
        try:
            saved_in = download(url, platform_name, mode, height, videos_dir, audios_dir, have_ffmpeg)
            print(f"\n{GREEN}[✔] Done! Saved in: {saved_in}{RESET}")
        except yt_dlp.utils.DownloadError as exc:
            explain_error(exc, platform_name)

        print(f"\n{GRAY}{'─' * min(60, term_cols() - 1)}{RESET}")


def main(argv=None) -> None:
    args = parse_args(argv)
    _setup_console()

    if args.version:
        print(f"{APP_NAME} {__version__}")
        return
    if args.check:
        banner()
        check(args)
        return

    try:
        run(args)
    except (KeyboardInterrupt, EOFError):
        print(f"\n\n{TEAL}Thanks for using {APP_NAME}. Bye!{RESET}")
        sys.exit(0)


if __name__ == "__main__":
    main()
