#!/usr/bin/env python3
"""Editing engine: automatic cuts, vertical 1080x1920, audio, slow-motion, sharpening, watermark, best-of.
Command line: python autocut.py <clips_folder> [--crop]
"""
import os, re, shutil, subprocess, sys
from pathlib import Path

MAX_LEN = 59.0         # max seconds (Shorts/TikTok)
FREEZE_MIN = 1.0       # frozen picture longer than X sec gets cut
MIN_SEG = 0.5          # drop pieces shorter than X sec
MIN_USEFUL = 3.0       # below this many useful seconds the clip is discarded
CRF = "14"             # quality (14 = almost lossless)
EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
SLOW_BEFORE, SLOW_AFTER, SLOW_SPEED = 0.4, 0.8, 0.5
SLOW_EXTRA = (SLOW_BEFORE + SLOW_AFTER) * (1 / SLOW_SPEED - 1)   # extra seconds caused by the slow-motion
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

DEFAULT_OPTS = {"audio": "original", "sharpen": True, "high": False, "watermark": "", "normalize": True}
MIN_REST = 12.0        # need at least X sec of leftover footage to make another part
MIN_PART = 8.0         # an extra part shorter than this is not worth it


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NOWIN)


# ---------------------------------------------------------------- analysis
def duration(ff, f):
    r = run([ff, "-i", str(f)])
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    if not m:
        return 0.0          # broken file or not a video
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def has_audio(ff, f):
    return "Audio:" in run([ff, "-i", str(f)]).stderr


def freezes(ff, f, dur):
    r = run([ff, "-i", str(f), "-vf", f"freezedetect=n=-50dB:d={FREEZE_MIN}", "-an", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"freeze_end: ([\d.]+)", r.stderr)]
    return [(s, ends[i] if i < len(ends) else dur) for i, s in enumerate(starts)]


def keep_segments(dur, frz):
    """Parts to keep (frozen pictures removed). May return empty if the whole clip is frozen."""
    segs, cur = [], 0.0
    for s, e in sorted(frz):
        if s - cur >= MIN_SEG:
            segs.append((cur, s))
        cur = max(cur, e)
    if dur - cur >= MIN_SEG:
        segs.append((cur, dur))
    return segs


def black_ratio(ff, f, dur):
    r = run([ff, "-i", str(f), "-vf", "blackdetect=d=0.5:pix_th=0.10", "-an", "-f", "null", "-"])
    tot = sum(float(x) for x in re.findall(r"black_duration:([\d.]+)", r.stderr))
    return tot / dur if dur else 0.0


def useless_reason(ff, f, dur, segs):
    kept = sum(e - s for s, e in segs)
    if kept < MIN_USEFUL:
        return f"too short or frozen ({kept:.1f}s useful)"
    if black_ratio(ff, f, dur) > 0.7:
        return "mostly black (menu or loading screen)"
    return None


def motion(ff, f):
    r = run([ff, "-i", str(f), "-vf", "select='gte(scene,0)',metadata=print:file=-", "-an", "-f", "null", "-"])
    txt = r.stdout + r.stderr
    pts = [float(x) for x in re.findall(r"pts_time:([\d.]+)", txt)]
    sc = [float(x) for x in re.findall(r"lavfi.scene_score=([\d.]+)", txt)]
    return list(zip(pts, sc))


def audio_levels(ff, f):
    """Volume every 0.1 s: list of (second, linear level). Empty if there is no audio."""
    if not has_audio(ff, f):
        return []
    r = run([ff, "-i", str(f), "-vn", "-af",
             "aresample=16000,asetnsamples=n=1600:p=0,astats=metadata=1:reset=1,"
             "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-", "-f", "null", "-"])
    txt = r.stdout + r.stderr
    pts = [float(x) for x in re.findall(r"pts_time:([\d.]+)", txt)]
    vals = re.findall(r"RMS_level=(-?[\d.]+|-inf|nan)", txt)
    out = []
    for t, v in zip(pts, vals):
        try:
            db = float(v)
            out.append((t, 0.0 if db < -90 else 10 ** (db / 20)))
        except ValueError:
            out.append((t, 0.0))
    return out


def in_segs(t, segs):
    return any(s <= t < e for s, e in segs)


def choose(ff, f, segs, max_len, need_peak=False):
    """If needed, keeps the most 'alive' window (motion + volume). Returns (segments, peak_moment)."""
    total = sum(e - s for s, e in segs)
    levels, mo = [], []
    if total > max_len or need_peak:
        levels = audio_levels(ff, f)
        if total > max_len or not levels:
            mo = motion(ff, f)
    if total > max_len:
        chunks = []
        for s, e in segs:
            t = s
            while t < e - 0.01:
                t2 = min(t + 1.0, e)
                m = sum(v for p, v in mo if t <= p < t2)
                a = max([v for p, v in levels if t <= p < t2] or [0.0])
                chunks.append((t, t2, m, a))
                t = t2
        mm = max([c[2] for c in chunks] or [0]) or 1.0
        aa = max([c[3] for c in chunks] or [0]) or 0.0
        wa = 0.5 if aa > 0 else 0.0
        scored = [(a, b, (1 - wa) * m / mm + (wa * c / aa if aa else 0.0)) for a, b, m, c in chunks]
        n = max(1, int(max_len))
        best, bi = -1.0, 0
        for i in range(0, max(1, len(scored) - n + 1)):
            v = sum(c[2] for c in scored[i:i + n])
            if v > best:
                best, bi = v, i
        segs2 = []
        for a, b, _ in scored[bi:bi + n]:
            if segs2 and abs(segs2[-1][1] - a) < 0.01:
                segs2[-1] = (segs2[-1][0], b)
            else:
                segs2.append((a, b))
        segs = segs2
    peak = None
    cand = [(v, t) for t, v in levels if in_segs(t, segs)]
    if cand and max(cand)[0] > 0:
        peak = max(cand)[1] + 0.05
    elif mo:
        cm = [(v, t) for t, v in mo if in_segs(t, segs)]
        if cm:
            peak = max(cm)[1]
    return segs, peak


def split_windows(ff, f, segs, max_len, count=3, need_peak=False):
    """From a long clip, gets up to 'count' parts (best first). Returns [(segments, peak)]."""
    out, rest = [], list(segs)
    while rest and len(out) < max(1, count):
        w, peak = choose(ff, f, rest, max_len, need_peak)
        if out and sum(e - s for s, e in w) < MIN_PART:
            break
        out.append((w, peak))
        lo, hi = min(s for s, e in w), max(e for s, e in w)
        nrest = []
        for s, e in rest:
            if e <= lo or s >= hi:
                nrest.append((s, e))
                continue
            if lo - s >= MIN_SEG:
                nrest.append((s, lo))
            if e - hi >= MIN_SEG:
                nrest.append((hi, e))
        rest = nrest
        if sum(e - s for s, e in rest) < MIN_REST:
            break
    return out


def make_cover(ff, video, dst):
    """Frame of the strongest moment (loudest) of the finished video, as a JPG image."""
    d = duration(ff, video)
    lv = audio_levels(ff, video)
    t = (max(lv, key=lambda x: x[1])[0] + 0.05) if lv else d / 2
    t = min(max(0.3, t), max(0.3, d - 0.3))
    r = run([ff, "-y", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(dst)])
    return r.returncode == 0 and Path(dst).exists()


def apply_slowmo(segs, peak):
    """Slows down briefly around the peak. Returns segments (start, end, speed)."""
    out, done = [], False
    for s, e in segs:
        if not done and peak is not None and s <= peak < e:
            a, b = max(s, peak - SLOW_BEFORE), min(e, peak + SLOW_AFTER)
            if b - a >= 0.6:
                if a - s > 0.05:
                    out.append((s, a, 1.0))
                out.append((a, b, SLOW_SPEED))
                if e - b > 0.05:
                    out.append((b, e, 1.0))
                done = True
                continue
        out.append((s, e, 1.0))
    return out


def norm_segs(segs):
    return [(x[0], x[1], x[2] if len(x) > 2 else 1.0) for x in segs]


def total_len(segs):
    return sum((e - s) / sp for s, e, sp in norm_segs(segs))


# ---------------------------------------------------------------- editing
def find_font():
    for p in ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"):
        if Path(p).exists():
            return p
    return None


def watermark_filter(text):
    text = re.sub(r"[^\w @._#-]", "", text or "").strip()
    if not text:
        return ""
    font = find_font()
    if not font:
        print("   (watermark skipped: font not found)")
        return ""
    font = font.replace("\\", "/").replace(":", "\\:")
    return (f",drawtext=fontfile='{font}':text='{text}':fontsize=40:fontcolor=white@0.6:"
            f"x=w-tw-36:y=170:shadowcolor=black@0.5:shadowx=2:shadowy=2")


def render(ff, f, segs, dst, crop, maxrate_k=None, opts=None):
    o = {**DEFAULT_OPTS, **(opts or {})}
    segs = norm_segs(segs)
    aud = o["audio"] != "muted" and has_audio(ff, f)
    parts = []
    for i, (s, e, sp) in enumerate(segs):
        parts.append(f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=(PTS-STARTPTS)/{sp}[v{i}]")
        if aud:
            at = f",atempo={sp}" if sp != 1.0 else ""
            parts.append(f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS{at}[a{i}]")
    n = len(segs)
    cat = "".join(f"[v{i}]" + (f"[a{i}]" if aud else "") for i in range(n))
    cat += f"concat=n={n}:v=1:a={1 if aud else 0}[cv]" + ("[ca]" if aud else "")
    sharp = ",unsharp=5:5:0.7:5:5:0.0,eq=contrast=1.04:saturation=1.08" if o["sharpen"] else ""
    wm = watermark_filter(o["watermark"])
    if crop:
        fin = f"[cv]scale=-2:1920:flags=lanczos,crop=1080:1920{sharp}{wm},setsar=1[v]"
    else:
        off = 170 if o["high"] else 0
        fin = ("[cv]split[bg][fg];"
               "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=40:6,eq=brightness=-0.08[b];"
               f"[fg]scale=1080:-2:flags=lanczos{sharp}[f];"
               f"[b][f]overlay=(W-w)/2:(H-h)/2-{off}{wm},setsar=1[v]")
    graph = ";".join(parts + [cat, fin])
    amap = "[ca]"
    if aud and o.get("normalize"):
        graph += ";[ca]aformat=channel_layouts=stereo,loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[an]"
        amap = "[an]"
    if aud and o["audio"] == "lowered":
        graph += f";{amap}volume=0.25[a]"
        amap = "[a]"
    cmd = [ff, "-y", "-i", str(f), "-filter_complex", graph, "-map", "[v]"]
    cmd += ["-map", amap] if aud else []
    cmd += ["-c:v", "libx264", "-preset", os.environ.get("SHORTS_PRESET", "medium"), "-crf", CRF,
            "-pix_fmt", "yuv420p", "-profile:v", "high"]
    if maxrate_k:
        cmd += ["-maxrate", f"{maxrate_k}k", "-bufsize", f"{2 * maxrate_k}k"]
    cmd += ["-c:a", "aac", "-b:a", "192k"] if aud else ["-an"]
    cmd += ["-movflags", "+faststart", str(dst)]
    r = run(cmd)
    if r.returncode != 0:
        print(r.stderr[-1500:])
        if Path(dst).exists():
            try:
                Path(dst).unlink()
            except OSError:
                pass
    return r.returncode == 0


def derive_muted(ff, src, dst):
    """Copy without audio and without re-encoding (instant)."""
    r = run([ff, "-y", "-i", str(src), "-an", "-c:v", "copy", "-movflags", "+faststart", str(dst)])
    return r.returncode == 0


# ---------------------------------------------------------------- best of
def _sig(ff, f):
    t = run([ff, "-i", str(f)]).stderr
    v = re.search(r"Video: (\w+).*?(\d{3,4}x\d{3,4}).*?([\d.]+) fps", t, re.S)
    a = re.search(r"Audio: (\w+).*?(\d+) Hz", t)
    return (v.groups() if v else None, a.groups() if a else None)


def best_of(ff, clips, dst, max_mb=45):
    """Joins several vertical clips into one video, without re-encoding when possible."""
    clips = [Path(c) for c in clips]
    sigs = [_sig(ff, c) for c in clips]
    durs = [duration(ff, c) for c in clips]
    total = sum(durs)
    all_audio = all(s[1] for s in sigs)
    same = all(s == sigs[0] for s in sigs) and all(s[0] for s in sigs)
    dst = Path(dst)
    if same:
        lst = dst.with_suffix(".txt")
        lst.write_text("".join("file '" + c.resolve().as_posix().replace("'", "'\\''") + "'\n" for c in clips), encoding="utf-8")
        r = run([ff, "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(dst)])
        try:
            lst.unlink()
        except OSError:
            pass
        if r.returncode == 0 and dst.stat().st_size <= 49 * 1024 * 1024:
            return True
    rate = int(min(20000, max_mb * 8 * 1024 / max(total, 1) - 192))
    cmd = [ff, "-y"]
    for c in clips:
        cmd += ["-i", str(c)]
    fg = []
    for i in range(len(clips)):
        fg.append(f"[{i}:v]fps=30,scale=1080:1920,setsar=1[v{i}]")
        if all_audio:
            fg.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
    cat = "".join(f"[v{i}]" + (f"[a{i}]" if all_audio else "") for i in range(len(clips)))
    cat += f"concat=n={len(clips)}:v=1:a={1 if all_audio else 0}[v]" + ("[a]" if all_audio else "")
    cmd += ["-filter_complex", ";".join(fg + [cat]), "-map", "[v]"] + (["-map", "[a]"] if all_audio else [])
    cmd += ["-c:v", "libx264", "-preset", os.environ.get("SHORTS_PRESET", "medium"), "-crf", CRF, "-pix_fmt", "yuv420p",
            "-maxrate", f"{rate}k", "-bufsize", f"{2 * rate}k"]
    cmd += ["-c:a", "aac", "-b:a", "192k"] if all_audio else ["-an"]
    cmd += ["-movflags", "+faststart", str(dst)]
    r = run(cmd)
    if r.returncode != 0:
        print(r.stderr[-1500:])
    return r.returncode == 0


# ---------------------------------------------------------------- command line
def main():
    if len(sys.argv) < 2:
        print("Usage: autocut.py <folder> [--crop]")
        return 1
    folder = Path(sys.argv[1])
    crop = "--crop" in sys.argv
    ff = shutil.which("ffmpeg")
    if not ff:
        print("FFmpeg not found. Install it with: winget install Gyan.FFmpeg")
        return 1
    outdir = folder / "Shorts_Ready"
    outdir.mkdir(exist_ok=True)
    for f in sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in EXT):
        print(f"\n>> {f.name}")
        o = outdir / (f.stem + "_short.mp4")
        if o.exists() and run([str(Path(ff).with_name("ffprobe" + Path(ff).suffix)), "-v", "error", str(o)]).returncode == 0:
            print("   already done, skipping")
            continue
        dur = duration(ff, f)
        segs = keep_segments(dur, freezes(ff, f, dur)) or [(0.0, dur)]
        segs, _ = choose(ff, f, segs, MAX_LEN)
        print(f"   original {dur:.1f}s -> final {total_len(segs):.1f}s ({len(segs)} pieces)")
        print("   OK" if render(ff, f, segs, o, crop) else "   ERROR", "->", o.name)
    print("\nDone. Videos in:", outdir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
