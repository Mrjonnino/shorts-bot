#!/usr/bin/env python3
"""YouTube (private) -> download -> vertical edit -> Telegram (as a file, no recompression)."""
import configparser, json, os, re, shutil, socket, subprocess, sys, threading, time
from pathlib import Path
import requests
import autocut as ac
import yt_api

BASE = Path(__file__).resolve().parent
CFG = BASE / "config.ini"
DONE = BASE / "done.txt"
STATS = BASE / "stats.json"
CLIPS = BASE / "clip_index.json"
SCHED = BASE / "to_delete.json"
FAILS = BASE / "failures.json"
OFFSET = BASE / "tg_offset.txt"
BEAT = BASE / "heartbeat.json"
LINKS = BASE / "manual_links.txt"
COOKIES = BASE / "cookies.txt"
WORK = BASE / "downloads"
OUT = BASE / "Shorts_Ready"
TG_LIMIT_MB = 45          # Telegram bots: max 50 MB, keep a margin
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_py = Path(sys.executable)
if _py.name.lower() == "pythonw.exe" and _py.with_name("python.exe").exists():
    _py = _py.with_name("python.exe")
YTDLP = [str(_py), "-m", "yt_dlp"]

_alerted = set()
_privacy = {}      # video id -> privacy (public/unlisted/private), filled from the API listing
_stats_lock = threading.Lock()

DEFAULTS = {
    "youtube": {"channel_id": "", "browser": "chrome", "cookie_source": "file", "make_unlisted": "yes", "delete_from_youtube": "no", "delete_after_days": "7", "title_filter": "Grand Theft Auto", "latest": "10"},
    "telegram": {"token": "", "chat_id": ""},
    "app": {"auto_minutes": "10", "summary_time": "22:00"},
    "video": {"styles": "blur", "audio": "original", "length": "59", "slowmo": "no", "sharpen": "yes",
              "action_high": "no", "watermark": "", "prefix": "GTA6", "cleanup_days": "14",
              "multi_shorts": "yes", "max_shorts": "3", "normalize": "yes", "cover": "yes"},
}


# Old (Italian) names -> new (English) names, so an existing installation keeps working.
_OLD_KEYS = {"cookie_da": "cookie_source", "rendi_non_in_elenco": "make_unlisted", "elimina_da_youtube": "delete_from_youtube",
             "elimina_dopo_giorni": "delete_after_days", "filtro_titolo": "title_filter", "ultimi": "latest",
             "auto_minuti": "auto_minutes", "riepilogo_ora": "summary_time", "stili": "styles", "durata": "length",
             "nitidezza": "sharpen", "azione_alta": "action_high", "filigrana": "watermark", "prefisso": "prefix",
             "pulizia_giorni": "cleanup_days", "piu_shorts": "multi_shorts", "normalizza": "normalize", "copertina": "cover"}
_OLD_VALUES = {"originale": "original", "abbassato": "lowered", "muto": "muted", "entrambi": "both"}
_OLD_FILES = {"fatti.txt": "done.txt", "link_manuali.txt": "manual_links.txt", "errori.json": "failures.json",
              "da_eliminare.json": "to_delete.json", "scaricati": "downloads", "Shorts_Pronti": "Shorts_Ready"}


def migrate_legacy():
    """One-time, harmless: renames old Italian config keys, values and files/folders to the English ones."""
    try:
        for old, new in _OLD_FILES.items():
            if (BASE / old).exists() and not (BASE / new).exists():
                (BASE / old).rename(BASE / new)
        if CFG.exists():
            text = CFG.read_text(encoding="utf-8")
            new_text = text
            for old, new in _OLD_KEYS.items():
                new_text = re.sub(rf"(?m)^{old}(\s*[=:])", rf"{new}\1", new_text)
            new_text = re.sub(r"(?m)^(audio\s*[=:]\s*)(originale|abbassato|muto|entrambi)\s*$",
                              lambda m: m.group(1) + _OLD_VALUES[m.group(2)], new_text)
            if new_text != text:
                CFG.write_text(new_text, encoding="utf-8")
        if STATS.exists():
            s = json.loads(STATS.read_text(encoding="utf-8"))
            if "giorni" in s or "riepilogo" in s:
                s = {"days": {d: {"sent": v.get("inviate", v.get("sent", 0)), "discarded": v.get("scartate", v.get("discarded", 0)),
                                  "index": v.get("indice", v.get("index", 0))} for d, v in s.get("giorni", s.get("days", {})).items()},
                     "summary": s.get("riepilogo", s.get("summary", ""))}
                STATS.write_text(json.dumps(s, indent=1), encoding="utf-8")
    except (OSError, ValueError) as e:
        print("   (migration skipped:", e, ")")


def load_cfg():
    migrate_legacy()
    c = configparser.ConfigParser(interpolation=None)
    c.read_dict(DEFAULTS)
    c.read(CFG, encoding="utf-8")
    return c


def save_cfg(cfg):
    with open(CFG, "w", encoding="utf-8") as f:
        cfg.write(f)


def flag(cfg, sec, key):
    return cfg.get(sec, key, fallback="no").strip().lower() in ("yes", "y", "1", "true", "on", "si", "sì")


def get_opts(cfg):
    try:
        durata = int(cfg.get("video", "length"))
    except ValueError:
        durata = 59
    stili = [s.strip() for s in cfg.get("video", "styles").split(",") if s.strip() in ("blur", "crop")] or ["blur"]
    audio = cfg.get("video", "audio").strip()
    if audio not in ("original", "lowered", "muted", "both"):
        audio = "original"
    try:
        maxs = int(cfg.get("video", "max_shorts"))
    except ValueError:
        maxs = 3
    return {"multi": flag(cfg, "video", "multi_shorts"), "max_shorts": max(1, min(5, maxs)),
            "normalize": flag(cfg, "video", "normalize"), "cover": flag(cfg, "video", "cover"),
            "styles": stili, "audio": audio, "length": max(5, min(59, durata)), "slowmo": flag(cfg, "video", "slowmo"),
            "sharpen": flag(cfg, "video", "sharpen"), "high": flag(cfg, "video", "action_high"),
            "watermark": cfg.get("video", "watermark").strip(),
            "prefix": re.sub(r"[^\w-]", "", cfg.get("video", "prefix")) or "clip"}


def auth_args(cfg):
    src = cfg.get("youtube", "cookie_source", fallback="file").strip().lower()
    if src in ("firefox", "edge", "chrome", "brave", "chromium", "opera", "vivaldi"):
        return ["--cookies-from-browser", src]
    if COOKIES.exists():
        return ["--cookies", str(COOKIES)]
    return ["--cookies-from-browser", cfg.get("youtube", "browser", fallback="chrome")]


REQUIRED_COOKIES = ("SID", "HSID", "SAPISID", "LOGIN_INFO")


def cookie_status():
    """Checks that cookies.txt contains the sign-in cookies. Returns (ok, missing). Reads names only."""
    if not COOKIES.exists():
        return False, list(REQUIRED_COOKIES)
    names = set()
    for line in COOKIES.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split("\t")
        if len(parts) >= 7 and not line.startswith("#"):
            names.add(parts[5])
    missing = [n for n in REQUIRED_COOKIES if n not in names]
    return not missing, missing


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NOWIN)



_caps = None
_updated = False


def find_deno():
    """Finds deno.exe even if the app started with an old PATH (installed after Windows started)."""
    p = shutil.which("deno")
    if p:
        return p
    lad = os.environ.get("LOCALAPPDATA", "")
    cands = [Path.home() / ".deno" / "bin" / "deno.exe", Path("C:/Program Files/deno/deno.exe")]
    if lad:
        cands.append(Path(lad) / "Microsoft" / "WinGet" / "Links" / "deno.exe")
        cands += list((Path(lad) / "Microsoft" / "WinGet" / "Packages").glob("DenoLand.Deno*/deno.exe"))
    for c in cands:
        if c.exists():
            return str(c)
    return None


def ytdlp_extra():
    """Extra yt-dlp options to solve YouTube's JavaScript challenges (only if the version supports them)."""
    global _caps
    if _caps is None:
        h = sh(YTDLP + ["--help"]).stdout
        extra = []
        d = find_deno()
        if "--js-runtimes" in h and d:
            extra += ["--js-runtimes", f"deno:{d}"]
        if "--remote-components" in h:
            extra += ["--remote-components", "ejs:github"]
        _caps = extra
    return _caps


def update_ytdlp():
    global _updated, _caps
    if _updated:
        return
    _updated = True
    print("   Updating yt-dlp (may take a minute)...")
    sh([str(_py), "-m", "pip", "install", "-q", "-U", "yt-dlp[default]"])
    _caps = None


def cookies_configured(cfg):
    src = cfg.get("youtube", "cookie_source", fallback="file").strip().lower()
    return src != "file" or COOKIES.exists()


def ydl(cfg, args, extra_player=None, use_cookies=True):
    cmd = YTDLP + (auth_args(cfg) if use_cookies else []) + ytdlp_extra()
    if extra_player:
        cmd += ["--extractor-args", "youtube:player_client=" + extra_player]
    return sh(cmd + args)


# ---------------------------------------------------------------- state and statistics
def done_ids():
    return set(DONE.read_text(encoding="utf-8").split()) if DONE.exists() else set()


def mark_done(vid):
    with open(DONE, "a", encoding="utf-8") as f:
        f.write(vid + "\n")


def _stats_load():
    try:
        return json.loads(STATS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"days": {}, "summary": ""}


def _stats_save(s):
    STATS.write_text(json.dumps(s, indent=1), encoding="utf-8")


def stat_add(kind, n=1):
    with _stats_lock:
        s = _stats_load()
        d = s["days"].setdefault(time.strftime("%Y-%m-%d"), {"sent": 0, "discarded": 0, "index": 0})
        d[kind] = d.get(kind, 0) + n
        _stats_save(s)


def next_index():
    with _stats_lock:
        s = _stats_load()
        d = s["days"].setdefault(time.strftime("%Y-%m-%d"), {"sent": 0, "discarded": 0, "index": 0})
        d["index"] = d.get("index", 0) + 1
        _stats_save(s)
        return d["index"]


def maybe_summary(cfg, now=None):
    """Once a day, at the chosen time, sends a Telegram message with how many clips were sent/discarded."""
    ora = cfg.get("app", "summary_time", fallback="").strip()
    if not ora or not cfg.get("telegram", "token", fallback="").strip():
        return False
    try:
        hh, mm = [int(x) for x in ora.split(":")]
    except ValueError:
        return False
    now = now or time.localtime()
    if (now.tm_hour, now.tm_min) < (hh, mm):
        return False
    day = time.strftime("%Y-%m-%d", now)
    with _stats_lock:
        s = _stats_load()
        if s.get("summary") == day:
            return False
        d = s["days"].get(day, {})
        if d.get("sent", 0) + d.get("discarded", 0) == 0:
            s["summary"] = day
            _stats_save(s)
            return False
    ok = telegram_text(cfg, f"📊 Today: {d.get('sent', 0)} clips sent, {d.get('discarded', 0)} discarded.")
    if ok:
        with _stats_lock:
            s = _stats_load()
            s["summary"] = day
            _stats_save(s)
    return ok


def cleanup(cfg):
    """Deletes finished videos older than N days and leftover downloads."""
    try:
        days = int(cfg.get("video", "cleanup_days", fallback="0"))
    except ValueError:
        days = 0
    n = 0
    now = time.time()
    for folder, age in ((OUT, days * 86400 if days > 0 else None), (WORK, 3 * 86400)):
        if age is None or not folder.exists():
            continue
        for f in folder.iterdir():
            try:
                if f.is_file() and now - f.stat().st_mtime > age:
                    f.unlink()
                    n += 1
            except OSError:
                pass
    if n:
        print(f"Cleanup: removed {n} old files.")
    return n


# ---------------------------------------------------------------- Telegram
def telegram_text(cfg, text):
    tok = cfg.get("telegram", "token").strip()
    chat = cfg.get("telegram", "chat_id").strip()
    try:
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", data={"chat_id": chat, "text": text}, timeout=30)
        return r.ok
    except requests.RequestException as e:
        print("!! Telegram unreachable:", e)
        return False


def telegram_send(cfg, path, caption):
    tok = cfg.get("telegram", "token").strip()
    chat = cfg.get("telegram", "chat_id").strip()
    try:
        with open(path, "rb") as fh:
            r = requests.post(f"https://api.telegram.org/bot{tok}/sendDocument",
                              data={"chat_id": chat, "caption": caption[:900]},
                              files={"document": (Path(path).name, fh, "video/mp4")}, timeout=1800)
    except requests.RequestException as e:
        print("!! Telegram unreachable:", e)
        return False
    if not r.ok:
        print("!! Telegram replied:", r.text[:400])
    return r.ok


def telegram_photo(cfg, path, caption=""):
    tok = cfg.get("telegram", "token").strip()
    chat = cfg.get("telegram", "chat_id").strip()
    try:
        with open(path, "rb") as fh:
            r = requests.post(f"https://api.telegram.org/bot{tok}/sendPhoto",
                              data={"chat_id": chat, "caption": caption[:900]}, files={"photo": fh}, timeout=120)
        return r.ok
    except requests.RequestException as e:
        print("!! Telegram unreachable:", e)
        return False


def set_chat_id(cfg):
    tok = cfg.get("telegram", "token").strip()
    r = requests.get(f"https://api.telegram.org/bot{tok}/getUpdates", timeout=30).json()
    chats = [u["message"]["chat"]["id"] for u in r.get("result", []) if "message" in u]
    if not chats:
        print("No message found. Open your bot in Telegram, write 'hi', then run this again.")
        return 1
    cfg.set("telegram", "chat_id", str(chats[-1]))
    save_cfg(cfg)
    print("Chat ID saved:", chats[-1])
    return 0


def alert(cfg, msg):
    """Prints and warns on Telegram (only once for the same problem)."""
    print("!!", msg)
    if msg not in _alerted:
        _alerted.add(msg)
        telegram_text(cfg, "⚠️ " + msg)


# ---------------------------------------------------------------- YouTube
def list_videos(cfg, info=None):
    info = info if info is not None else {}
    info.update({"all": 0, "filtered": []})
    vids = []
    n = cfg.get("youtube", "latest", fallback="10")
    used_api = False
    if yt_api.is_connected():
        try:
            for vid, title, priv in yt_api.list_uploads(int(n)):
                vids.append((vid, title))
                _privacy[vid] = priv
            used_api = True
        except yt_api.AuthError as e:
            alert(cfg, str(e))
        except Exception as e:
            print("!! Official YouTube access failed:", e)
    if not used_api:
        ch = cfg.get("youtube", "channel_id", fallback="").strip()
        m = re.search(r"(UC[\w-]{22})", ch)
        ch = m.group(1) if m else ch
        if ch.startswith("UC"):
            url = "https://www.youtube.com/playlist?list=UU" + ch[2:]
            r = ydl(cfg, ["--flat-playlist", "--dump-single-json", "--playlist-end", n, url])
            if r.returncode == 0:
                for e in json.loads(r.stdout).get("entries", []):
                    vids.append((e["id"], e.get("title") or e["id"]))
            else:
                print(r.stderr[-600:])
                alert(cfg, "Cannot read the video list: the YouTube cookies have probably expired. "
                           "Redo cookies.txt, or connect YouTube from Settings.")
                if not LINKS.exists():
                    return None
        elif not LINKS.exists():
            print("!! The channel_id is missing in Settings.")
            return None
    if LINKS.exists():
        for line in LINKS.read_text(encoding="utf-8").split():
            m = re.search(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})", line)
            if m:
                vids.append((m.group(1), m.group(1)))
    flt = cfg.get("youtube", "title_filter", fallback="").strip().lower()
    seen, out = set(), []
    for vid, title in vids:
        if vid in seen:
            continue
        seen.add(vid)
        info["all"] += 1
        if flt and title != vid and flt not in title.lower():
            info["filtered"].append((vid, title))
            continue
        out.append((vid, title))
    return out


def download(cfg, vid):
    WORK.mkdir(exist_ok=True)
    tpl = str(WORK / "%(id)s.%(ext)s")
    args = ["--no-playlist", "-f", "bv*+ba/b", "--merge-output-format", "mp4",
            "-o", tpl, "https://www.youtube.com/watch?v=" + vid]
    f = WORK / f"{vid}.mp4"
    # private -> 'unlisted' (downloadable without cookies, the link is enough)
    if _privacy.get(vid) == "private" and yt_api.is_connected() and flag(cfg, "youtube", "make_unlisted"):
        try:
            if yt_api.make_unlisted(vid):
                print("   Private video: setting it to 'unlisted' so it can be downloaded without cookies.")
                _privacy[vid] = "unlisted"
        except Exception as e:
            print("!! Cannot change the privacy:", e)
    no_cookie_ok = yt_api.is_connected() and _privacy.get(vid) in ("unlisted", "public")
    modes = [False] + ([True] if cookies_configured(cfg) else []) if no_cookie_ok else [True]
    err = ""
    for use_cookies in modes:
        r = ydl(cfg, args, use_cookies=use_cookies)
        if r.returncode == 0 and f.exists():
            return f
        update_ytdlp()                       # once per start: yt-dlp must be kept up to date
        r = ydl(cfg, args, use_cookies=use_cookies)
        if r.returncode == 0 and f.exists():
            return f
        print("   Retrying with another YouTube method...")
        r = ydl(cfg, args, extra_player="tv,web_safari,mweb", use_cookies=use_cookies)
        if r.returncode == 0 and f.exists():
            return f
        err = r.stderr or ""
    print(err[-900:])
    if "Only images" in err or "JavaScript" in err or "EJS" in err:
        d = find_deno()
        print("!! yt-dlp cannot solve YouTube's JavaScript challenge. "
              + (f"Deno found in {d}." if d else "Deno NOT found: run Install.bat."))
    return None


# ---------------------------------------------------------------- elaborazione
_json_lock = threading.Lock()


def _jload(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _jsave(p, data):
    Path(p).write_text(json.dumps(data, indent=1), encoding="utf-8")


def record_clip(key, vid, title, src):
    with _json_lock:
        d = _jload(CLIPS, {})
        d[key] = {"vid": vid, "title": title, "src": str(src), "t": time.time()}
        d["last"] = key
        keys = sorted((k for k in d if k != "last"), key=lambda k: d[k].get("t", 0))
        for k in keys[:-60]:
            d.pop(k, None)
        _jsave(CLIPS, d)


def bump_failures(vid):
    """Counts failed attempts per clip: after 3 it gives up (so it does not repeat forever)."""
    with _json_lock:
        d = _jload(FAILS, {})
        d[vid] = d.get(vid, 0) + 1
        _jsave(FAILS, d)
        return d[vid]


def schedule_delete(vid):
    with _json_lock:
        d = _jload(SCHED, {})
        d.setdefault(vid, time.time())
        _jsave(SCHED, d)


def youtube_cleanup(cfg):
    """Optional: deletes already-sent clips from YouTube after N days (cannot be undone)."""
    if not flag(cfg, "youtube", "delete_from_youtube") or not yt_api.is_connected():
        return 0
    try:
        days = max(1, int(cfg.get("youtube", "delete_after_days", fallback="7")))
    except ValueError:
        days = 7
    with _json_lock:
        d = _jload(SCHED, {})
    n, now = 0, time.time()
    for vid, ts in list(d.items()):
        if now - ts < days * 86400:
            continue
        try:
            yt_api.delete_video(vid)
        except yt_api.AuthError as e:
            alert(cfg, "Cannot delete videos from YouTube: " + str(e))
            break
        except Exception as e:
            if "404" not in str(e):
                print("!! Deleting from YouTube failed:", e)
                continue
        d.pop(vid, None)
        n += 1
    with _json_lock:
        _jsave(SCHED, d)
    if n:
        print(f"YouTube: deleted {n} already-sent clips (older than {days} days).")
    return n


def process_clip(ff, cfg, src, vid, title, o, redo=False):
    """Edits and sends a clip (several parts if it is long). Returns 'sent', 'discarded' or 'error'."""
    dur = ac.duration(ff, src)
    if dur <= 0:
        print("   Video file is damaged or incomplete.")
        _rm(src)
        raise ValueError("damaged video")
    segs = ac.keep_segments(dur, ac.freezes(ff, src, dur))
    why = ac.useless_reason(ff, src, dur, segs)
    if why:
        print(f"   Scartata: {why}")
        if not redo:
            mark_done(vid)
            stat_add("discarded")
            _rm(src)
        return "discarded"
    budget = o["length"] - (ac.SLOW_EXTRA if o["slowmo"] else 0)
    if o.get("multi") and not redo:
        wins = ac.split_windows(ff, src, segs, budget, o["max_shorts"], need_peak=o["slowmo"])
    else:
        wins = [ac.choose(ff, src, segs, budget, need_peak=o["slowmo"])]
    n = len(wins)
    if n > 1:
        print(f"   Long clip: making {n} different Shorts")
    day = time.strftime("%Y-%m-%d")
    ro = {"sharpen": o["sharpen"], "high": o["high"], "watermark": o["watermark"], "normalize": o["normalize"],
          "audio": "original" if o["audio"] == "both" else o["audio"]}
    sent_any = False
    OUT.mkdir(exist_ok=True)
    for k, (wsegs, peak) in enumerate(wins, 1):
        if o["slowmo"] and peak is not None:
            wsegs = ac.apply_slowmo(wsegs, peak)
            print(f"   Slow-motion on the action moment ({peak:.1f}s)")
        final = ac.total_len(wsegs)
        rate = int(min(20000, TG_LIMIT_MB * 8 * 1024 / final - 192))
        part = f" part {k}/{n}" if n > 1 else ""
        print(f"   Editing{part}: {dur:.1f}s -> {final:.1f}s")
        idx = next_index()
        key = f"{o['prefix']}_{day}_{idx:02d}"
        first_ok = None
        for style in o["styles"]:
            tag = "blur" if style == "blur" else "crop"
            base = f"{key}_{tag}"
            dst = OUT / f"{base}.mp4"
            print(f"   Making the {tag} version...")
            if not ac.render(ff, src, wsegs, dst, style == "crop", rate, ro):
                print(f"!! ERROR while editing ({tag})")
                continue
            if dst.stat().st_size > 49 * 1024 * 1024:
                print("   Too big, retrying with a lower bitrate...")
                ac.render(ff, src, wsegs, dst, style == "crop", int(rate * 0.75), ro)
            first_ok = first_ok or dst
            files = [(dst, f"{title}\n[{tag}]{part}")]
            if o["audio"] == "both":
                m = OUT / f"{base}_muted.mp4"
                if ac.derive_muted(ff, dst, m):
                    files.append((m, f"{title}\n[{tag}, no audio]{part}"))
            for p, cap in files:
                print(f"   Sending to Telegram: {p.name} ({p.stat().st_size / 1e6:.1f} MB)...")
                if telegram_send(cfg, p, cap):
                    sent_any = True
                else:
                    print(f"!! Sending failed: {p.name}")
        if first_ok is not None:
            record_clip(key, vid, title, src)
            if o["cover"]:
                cov = OUT / f"{key}_cover.jpg"
                if ac.make_cover(ff, first_ok, cov):
                    telegram_photo(cfg, cov, f"Cover{part}")
    if sent_any:
        if not redo:
            mark_done(vid)
            stat_add("sent")
            if flag(cfg, "youtube", "delete_from_youtube"):
                schedule_delete(vid)
        print("   Sent." + ("" if redo else " (the original is kept for 3 days, for the Telegram commands)"))
        return "sent"
    return "error"


# ---------------------------------------------------------------- health check
def beat(**kw):
    with _json_lock:
        d = _jload(BEAT, {})
        d.update(kw)
        _jsave(BEAT, d)


def watchdog(cfg, auto_min=10):
    """If the app is in automatic mode but stopped checking, or a check is stuck, warns on Telegram (once)."""
    d = _jload(BEAT, {})
    now = time.time()
    run = d.get("running_since")
    if run and now - run > 45 * 60 and d.get("alert_run") != run:
        telegram_text(cfg, "⚠️ A check has been running for over 45 minutes: it may be stuck "
                           "(download or editing frozen). Try 'Stop' and then 'Check now'.")
        beat(alert_run=run)
        return "bloccato"
    last = d.get("last_ok")
    limit = max(30, 3 * int(auto_min)) * 60
    if last and not run and now - last > limit and d.get("alert_ok") != last:
        telegram_text(cfg, f"⚠️ I have not been able to check YouTube for {int((now - last) // 60)} minutes "
                           "(expired connection, no network or an error). Open the app and press 'Check everything'.")
        beat(alert_ok=last)
        return "fermo"
    return None


def startup_notice(cfg):
    """At app start: if the PC/app was off for a long time, says so (then catches up on missed clips by itself)."""
    d = _jload(BEAT, {})
    last = d.get("last_ok")
    if last and time.time() - last > 3 * 3600 and cfg.get("telegram", "token", fallback="").strip():
        telegram_text(cfg, f"ℹ️ App restarted after {int((time.time() - last) // 3600)} hours: "
                           "checking for missed clips right now.")
        return True
    return False


def diagnostics(cfg):
    """Checks everything that is needed. Returns [(ok, text)]."""
    out = []
    add = lambda ok, t: out.append((bool(ok), t))
    ff = shutil.which("ffmpeg")
    add(ff, "FFmpeg found" if ff else "FFmpeg NOT found: run Install.bat")
    add(shutil.which("ffprobe") or ff, "ffprobe found" if shutil.which("ffprobe") else "ffprobe not found (usually next to FFmpeg)")
    r = sh(YTDLP + ["--version"])
    add(r.returncode == 0, "yt-dlp " + r.stdout.strip() if r.returncode == 0 else "yt-dlp does NOT work: run Install.bat")
    d = find_deno()
    add(d, "Deno found" if d else "Deno NOT found (YouTube needs it): run Install.bat")
    if yt_api.is_connected():
        try:
            n = len(yt_api.list_uploads(5))
            add(True, f"YouTube connected: I can see the channel videos ({n} in the last list)")
        except Exception as e:
            add(False, "YouTube connected but not answering: " + str(e) + " -> Settings, Connect YouTube")
    else:
        add(False, "YouTube NOT connected (Settings, Connect YouTube): cookies are used instead, and they expire")
    tok = cfg.get("telegram", "token", fallback="").strip()
    chat = cfg.get("telegram", "chat_id", fallback="").strip()
    if not tok:
        add(False, "Telegram: the token is missing (Settings)")
    else:
        try:
            j = requests.get(f"https://api.telegram.org/bot{tok}/getMe", timeout=20).json()
            add(j.get("ok"), "Telegram: bot reachable" if j.get("ok") else "Telegram: the token is not valid")
        except requests.RequestException:
            add(False, "Telegram: unreachable (no internet?)")
        add(chat, "Telegram: chat ID set" if chat else "Telegram: the chat ID is missing (Settings, Find chat ID)")
    free = shutil.disk_usage(BASE).free / 1e9
    add(free >= 5, f"Free disk space: {free:.0f} GB" + ("" if free >= 5 else " (low: free some space)"))
    try:
        OUT.mkdir(exist_ok=True)
        t = OUT / ".prova"
        t.write_text("x")
        t.unlink()
        add(True, "Video folder is writable")
    except OSError:
        add(False, "Cannot write to the Shorts_Ready folder")
    d = _jload(BEAT, {})
    if d.get("last_ok"):
        add(True, "Last successful check: " + time.strftime("%d/%m %H:%M", time.localtime(d["last_ok"])))
    return out


def diagnostics_text(cfg):
    return "\n".join(("✔ " if ok else "✘ ") + t for ok, t in diagnostics(cfg))


# ---------------------------------------------------------------- Telegram commands
HELP_TG = ("Reply to a video (or just write) with:\n"
           "short / medium / long = length 20 / 40 / 59 s\n"
           "slow = slow motion, noslow = without\n"
           "mute = no audio, crop or blur = framing style\n"
           "redo = edit it again (only the words you add change things)\n"
           "status = clips today, check = verify everything works, help = this message\n"
           "If you do not reply to a video, the latest clip is used. The original is kept for 3 days.\n"
           "(Italian words work too: corto, medio, lungo, muto, ritaglio, sfocato, rifai, stato, controlla, aiuto.)")


def parse_command(text):
    """Returns (action, changes) or (None, None). action: 'help', 'status', 'diagnosis' or 'redo'."""
    w = set(re.findall(r"[a-zà-ù0-9]+", (text or "").lower()))
    if w & {"help", "start", "commands", "aiuto", "comandi"}:
        return "help", {}
    if w & {"status", "stato"}:
        return "status", {}
    if w & {"check", "health", "controlla", "diagnosi", "salute"}:
        return "diagnosis", {}
    ov = {}
    if w & {"short", "corto", "breve"}:
        ov["length"] = 20
    if w & {"medium", "medio"}:
        ov["length"] = 40
    if w & {"long", "full", "lungo", "intero"}:
        ov["length"] = 59
    if w & {"slow", "slowmo", "slowmotion", "rallenta", "rallentatore"}:
        ov["slowmo"] = True
    if w & {"noslow", "senzaslow"}:
        ov["slowmo"] = False
    if w & {"mute", "muted", "noaudio", "muto", "senzaaudio"}:
        ov["audio"] = "muted"
    if w & {"crop", "ritaglio"}:
        ov["styles"] = ["crop"]
    if w & {"blur", "blurred", "sfocato"}:
        ov["styles"] = ["blur"]
    if ov or w & {"redo", "again", "remake", "rifai", "rimonta", "ancora"}:
        return "redo", ov
    return None, None


def _tg_get(cfg, offset=None):
    tok = cfg.get("telegram", "token").strip()
    params = {"timeout": 0, "allowed_updates": json.dumps(["message"])}
    if offset is not None:
        params["offset"] = offset
    return requests.get(f"https://api.telegram.org/bot{tok}/getUpdates", params=params, timeout=30).json().get("result", [])


def _my_messages(cfg, updates):
    chat = cfg.get("telegram", "chat_id").strip()
    return [u for u in updates if str(u.get("message", {}).get("chat", {}).get("id", "")) == chat]


def has_new_commands(cfg):
    """Light check: is there any new message for the bot? (does not consume it)"""
    try:
        if not cfg.get("telegram", "token", fallback="").strip() or not OFFSET.exists():
            return False
        off = int(OFFSET.read_text().strip() or 0)
        return any(m["message"].get("text") for m in _my_messages(cfg, _tg_get(cfg, off)))
    except (requests.RequestException, ValueError, OSError):
        return False


def _handle_message(cfg, ff, msg):
    action, ov = parse_command(msg.get("text", ""))
    if action is None:
        telegram_text(cfg, "I did not understand. " + HELP_TG)
        return
    if action == "help":
        telegram_text(cfg, HELP_TG)
        return
    if action == "diagnosis":
        telegram_text(cfg, diagnostics_text(cfg))
        return
    if action == "status":
        d = _stats_load()["days"].get(time.strftime("%Y-%m-%d"), {})
        telegram_text(cfg, f"Today: {d.get('sent', 0)} clips sent, {d.get('discarded', 0)} discarded.")
        return
    idx = _jload(CLIPS, {})
    key = None
    rep = (msg.get("reply_to_message") or {}).get("document") or {}
    m = re.match(r"^(.+?_\d{4}-\d{2}-\d{2}_\d{2})_", rep.get("file_name", ""))
    if m:
        key = m.group(1)
    elif (msg.get("reply_to_message") or {}).get("photo") or (msg.get("reply_to_message") or {}).get("caption", "").startswith("Cover"):
        key = idx.get("last")
    else:
        key = idx.get("last")
    info = idx.get(key) if key else None
    if not info:
        telegram_text(cfg, "I cannot find the clip to redo (reply to a recent video).")
        return
    src = Path(info["src"])
    if not src.exists():
        telegram_text(cfg, "The original is gone (it is deleted after 3 days).")
        return
    o = get_opts(cfg)
    o.update(ov)
    o["multi"] = False
    telegram_text(cfg, "OK, re-editing the clip" + (f" ({', '.join(f'{k}={v}' for k, v in ov.items())})" if ov else "") + "...")
    res = process_clip(ff, cfg, src, info["vid"], info["title"] + " (redone)", o, redo=True)
    if res != "sent":
        telegram_text(cfg, "I could not redo the clip.")


def _poll(cfg):
    """Reads new messages from the bot and runs the commands. Must be called with the lock already taken."""
    if not cfg.get("telegram", "token", fallback="").strip():
        return 0
    ff = shutil.which("ffmpeg")
    try:
        if not OFFSET.exists():                       # first time: ignore the old history
            ups = _tg_get(cfg)
            OFFSET.write_text(str(max([u["update_id"] for u in ups] or [0]) + 1))
            return 0
        off = int(OFFSET.read_text().strip() or 0)
        ups = _tg_get(cfg, off)
    except (requests.RequestException, ValueError, OSError) as e:
        print("!! Telegram (comandi):", e)
        return 0
    done = 0
    mine = {u["update_id"] for u in _my_messages(cfg, ups)}
    for u in ups:
        OFFSET.write_text(str(u["update_id"] + 1))
        if u["update_id"] not in mine or not u["message"].get("text"):
            continue
        if not ff:
            continue
        print("Telegram command:", u["message"]["text"][:60])
        try:
            _handle_message(cfg, ff, u["message"])
            done += 1
        except Exception as e:
            print("!! Error in the command:", e)
            telegram_text(cfg, "Error in the command: " + str(e)[:200])
    return done


def _rm(p):
    try:
        Path(p).unlink()
    except OSError:
        pass


def _try_lock():
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", 47654))
        return lock
    except OSError:
        lock.close()
        return None


def poll_commands(cfg):
    lock = _try_lock()
    if not lock:
        return 0
    try:
        return _poll(cfg)
    finally:
        lock.close()


def run_once(cfg, stop=None):
    """Checks YouTube, edits and sends the new clips. Returns how many were sent.
    One check at a time: if another copy of the app is already working, it exits."""
    lock = _try_lock()
    if not lock:
        print("Another check is already running (another app window?), skipping.")
        return 0
    beat(running_since=time.time())
    try:
        return _run_once(cfg, stop)
    finally:
        beat(running_since=None)
        lock.close()


def _run_once(cfg, stop=None):
    if not cfg.get("telegram", "token", fallback="").strip() or not cfg.get("telegram", "chat_id", fallback="").strip():
        print("!! The Telegram token or chat ID is missing (Settings).")
        return 0
    ff = shutil.which("ffmpeg")
    if not ff:
        print("!! FFmpeg not found: run Install.bat.")
        return 0
    OUT.mkdir(exist_ok=True)
    cleanup(cfg)
    youtube_cleanup(cfg)
    _poll(cfg)
    ok_c, missing = cookie_status()
    if not yt_api.is_connected() and cfg.get("youtube", "cookie_source", fallback="file") == "file" and COOKIES.exists() and not ok_c:
        print("!! cookies.txt is incomplete (missing: " + ", ".join(missing) + "): private videos will not show. "
              "Export it again from the YouTube Studio page (channel content), in incognito.")
    info = {}
    vids = list_videos(cfg, info)
    if vids is None:
        return 0
    beat(last_ok=time.time())
    _alerted.clear()
    done = done_ids()
    new = [v for v in vids if v[0] not in done]
    if not new:
        print(f"No new clips. I saw {info['all']} videos on the channel: "
              f"{len(vids) - len(new)} already sent, {len(info['filtered'])} excluded by the title filter.")
        if info["filtered"]:
            print("   Excluded by the filter:", "; ".join(t for _, t in info["filtered"][:5]))
        if info["all"] == 0:
            print("   The list is empty: YouTube does not show private videos with these cookies, or the channel ID "
                  "is wrong. Try pasting the video link into manual_links.txt.")
        return 0
    o = get_opts(cfg)
    sent = discarded = 0
    for vid, title in reversed(new):          # oldest first
        if stop is not None and stop.is_set():
            print("Interrotto.")
            break
        print(f"\n>> {title} [{vid}]")
        print("   Downloading from YouTube...")
        src = download(cfg, vid)
        if not src:
            alert(cfg, f"Download failed for '{title}'. Check cookies.txt (or reconnect YouTube).")
            continue
        try:
            res = process_clip(ff, cfg, src, vid, title, o)
        except Exception as e:                      # one broken clip must not stop the others
            n = bump_failures(vid)
            print(f"!! Error on this clip ({n}/3): {e}")
            if n >= 3:
                mark_done(vid)
                alert(cfg, f"I gave up on the clip '{title}' after 3 failed attempts ({e}).")
            continue
        sent += res == "sent"
        discarded += res == "discarded"
    print(f"\nFinito: {sent} inviate, {discarded} scartate.")
    return sent


def best_of_send(cfg, files):
    """Joins the chosen clips into one video and sends it to Telegram."""
    ff = shutil.which("ffmpeg")
    if not ff:
        print("!! FFmpeg not found.")
        return False
    o = get_opts(cfg)
    OUT.mkdir(exist_ok=True)
    dst = OUT / f"{o['prefix']}_bestof_{time.strftime('%Y-%m-%d_%H%M')}.mp4"
    print(f"Unisco {len(files)} clip...")
    if not ac.best_of(ff, files, dst, TG_LIMIT_MB):
        print("!! ERROR while joining the clips")
        return False
    print(f"Sending to Telegram: {dst.name} ({dst.stat().st_size / 1e6:.1f} MB)...")
    ok = telegram_send(cfg, dst, "Best of")
    print("Sent." if ok else "!! Sending failed")
    return ok


def main():
    cfg = load_cfg()
    if "--chat-id" in sys.argv:
        return set_chat_id(cfg)
    if ("--diagnosis" in sys.argv or "--diagnosi" in sys.argv):
        print(diagnostics_text(cfg))
        return 0
    if "--crop" in sys.argv:
        cfg.set("video", "styles", "crop")
    run_once(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
