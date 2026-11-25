#!/usr/bin/env python3
"""Shorts Bot - private YouTube clips -> vertical edit -> Telegram. Double-click to open."""
import contextlib, os, queue, shutil, subprocess, sys, threading, time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline as pl
import yt_api

DONE = object()
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
AUDIO = {"Original": "original", "Lowered": "lowered", "No audio": "muted",
         "Original + a no-audio version": "both"}
COOKIE_SRC = {"cookies.txt file": "file", "Firefox": "firefox", "Edge": "edge", "Chrome": "chrome"}
LENGTH = {"15 seconds": 15, "20 seconds": 20, "30 seconds": 30, "Up to 59 seconds": 59}


def startup_lnk():
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Shorts Bot.lnk"


def set_startup(on):
    """Creates or removes the shortcut in the Windows Startup folder."""
    lnk = startup_lnk()
    if not on:
        if lnk.exists():
            lnk.unlink()
        return
    pyw = Path(sys.executable)
    if pyw.name.lower() == "python.exe" and pyw.with_name("pythonw.exe").exists():
        pyw = pyw.with_name("pythonw.exe")
    script = Path(__file__).resolve()
    ps = ("$s=(New-Object -COM WScript.Shell).CreateShortcut('%s');$s.TargetPath='%s';"
          "$s.Arguments='\"%s\" --auto';$s.WorkingDirectory='%s';$s.WindowStyle=7;$s.Save()"
          % (lnk, pyw, script, script.parent))
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], creationflags=NOWIN)


class Writer:
    def __init__(self, q):
        self.q = q

    def write(self, s):
        if s:
            self.q.put(s)

    def flush(self):
        pass


def key_of(mapping, value, default):
    for k, v in mapping.items():
        if v == value:
            return k
    return default


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Shorts Bot  -  GTA 6 -> Telegram")
        self.geometry("820x700")
        self.minsize(760, 620)
        try:
            ttk.Style(self).theme_use("vista")
        except tk.TclError:
            pass
        self.q = queue.Queue()
        self.t0 = time.time()
        self.running = False
        self.last_sent = 0
        self.stop_ev = threading.Event()
        self.after_id = None
        self.cfg = pl.load_cfg()
        c = self.cfg
        styles = c.get("video", "styles")
        self.blur_var = tk.BooleanVar(value="blur" in styles or "crop" not in styles)
        self.crop_var = tk.BooleanVar(value="crop" in styles)
        self.audio_var = tk.StringVar(value=key_of(AUDIO, c.get("video", "audio"), "Original"))
        self.length_var = tk.StringVar(value=key_of(LENGTH, int(c.get("video", "length") or 59), "Up to 59 seconds"))
        self.slow_var = tk.BooleanVar(value=pl.flag(c, "video", "slowmo"))
        self.sharp_var = tk.BooleanVar(value=pl.flag(c, "video", "sharpen"))
        self.high_var = tk.BooleanVar(value=pl.flag(c, "video", "action_high"))
        self.wm_var = tk.StringVar(value=c.get("video", "watermark"))
        self.multi_var = tk.BooleanVar(value=pl.flag(c, "video", "multi_shorts"))
        self.norm_var = tk.BooleanVar(value=pl.flag(c, "video", "normalize"))
        self.cov_var = tk.BooleanVar(value=pl.flag(c, "video", "cover"))
        self.auto_var = tk.BooleanVar(value=False)
        self.min_var = tk.IntVar(value=int(c.get("app", "auto_minutes") or 10))
        self.next_var = tk.StringVar(value="")
        self.cookie_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=10)
        self.tab_main = ttk.Frame(nb, padding=12)
        self.tab_set = ttk.Frame(nb, padding=16)
        nb.add(self.tab_main, text="  Main  ")
        nb.add(self.tab_set, text="  Settings  ")
        self.build_main()
        self.build_settings()

        ttk.Label(self, textvariable=self.status_var, anchor="w", relief="sunken").pack(fill="x", side="bottom")
        self.refresh_cookies()
        self.after(100, self.poll)
        self.after(5000, self.tick)
        self.after(25000, self.cmd_tick)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        if not shutil.which("ffmpeg"):
            self.log("!! FFmpeg not found: run Install.bat\n")
        if "--auto" in sys.argv:
            self.auto_var.set(True)
            self.iconify()
            self.log("Automatic start: first check in 20 seconds.\n")
            self.after(20000, self.start)
            threading.Thread(target=lambda: pl.startup_notice(pl.load_cfg()), daemon=True).start()

    # ---------- main screen ----------
    def build_main(self):
        f = self.tab_main
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.btn = ttk.Button(top, text="▶  Check now", command=self.start)
        self.btn.pack(side="left", ipadx=14, ipady=6)
        ttk.Button(top, text="Stop", command=self.stop_ev.set).pack(side="left", padx=8, ipady=6)
        ttk.Button(top, text="Open video folder", command=lambda: self.open_dir(pl.OUT)).pack(side="right", ipady=6)
        self.btn_best = ttk.Button(top, text="Create best of…", command=self.best_of)
        self.btn_best.pack(side="right", padx=8, ipady=6)
        ttk.Button(top, text="Check everything", command=self.check_all).pack(side="right", ipady=6)

        vid = ttk.LabelFrame(f, text=" Video ", padding=10)
        vid.pack(fill="x", pady=(10, 0))
        ttk.Label(vid, text="Versions:").grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(vid, text="Blurred background (whole frame visible)", variable=self.blur_var,
                        command=self.save_opts).grid(row=0, column=1, columnspan=2, sticky="w", padx=8)
        ttk.Checkbutton(vid, text="Full-screen crop", variable=self.crop_var,
                        command=self.save_opts).grid(row=0, column=3, columnspan=2, sticky="w")
        ttk.Label(vid, text="Audio:").grid(row=1, column=0, sticky="w", pady=6)
        cb = ttk.Combobox(vid, state="readonly", width=32, values=list(AUDIO), textvariable=self.audio_var)
        cb.grid(row=1, column=1, columnspan=2, sticky="w", padx=8)
        cb.bind("<<ComboboxSelected>>", lambda e: self.save_opts())
        ttk.Label(vid, text="Length:").grid(row=1, column=3, sticky="e")
        cd = ttk.Combobox(vid, state="readonly", width=18, values=list(LENGTH), textvariable=self.length_var)
        cd.grid(row=1, column=4, sticky="w", padx=8)
        cd.bind("<<ComboboxSelected>>", lambda e: self.save_opts())
        ttk.Checkbutton(vid, text="Slow motion on the action moment", variable=self.slow_var,
                        command=self.save_opts).grid(row=2, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(vid, text="Sharper image and better colors", variable=self.sharp_var,
                        command=self.save_opts).grid(row=2, column=3, columnspan=2, sticky="w")
        ttk.Checkbutton(vid, text="Action shifted up (room for captions)", variable=self.high_var,
                        command=self.save_opts).grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Label(vid, text="Watermark:").grid(row=3, column=3, sticky="e", pady=(4, 0))
        fe = ttk.Entry(vid, width=20, textvariable=self.wm_var)
        fe.grid(row=3, column=4, sticky="w", padx=8, pady=(4, 0))
        fe.bind("<FocusOut>", lambda e: self.save_opts())
        ttk.Checkbutton(vid, text="Long clip = several Shorts (up to 3)", variable=self.multi_var,
                        command=self.save_opts).grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Checkbutton(vid, text="Even volume (handy for adding music)", variable=self.norm_var,
                        command=self.save_opts).grid(row=4, column=3, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Checkbutton(vid, text="Also send a cover image (strongest frame)", variable=self.cov_var,
                        command=self.save_opts).grid(row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))

        opt = ttk.LabelFrame(f, text=" Automatic ", padding=10)
        opt.pack(fill="x", pady=8)
        ttk.Checkbutton(opt, text="Automatic mode: check every", variable=self.auto_var,
                        command=self.toggle_auto).grid(row=0, column=0, sticky="w")
        ttk.Spinbox(opt, from_=2, to=120, width=4, textvariable=self.min_var, command=self.save_opts).grid(row=0, column=1, padx=4)
        ttk.Label(opt, text="minutes (while the app is open)").grid(row=0, column=2, sticky="w")
        ttk.Label(opt, textvariable=self.next_var, foreground="#555").grid(row=1, column=0, columnspan=4, sticky="w")
        self.boot_var = tk.BooleanVar(value=startup_lnk().exists())
        ttk.Checkbutton(opt, text="Start with Windows (starts by itself, minimized, in automatic mode)",
                        variable=self.boot_var, command=self.toggle_boot).grid(row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))

        self.logbox = scrolledtext.ScrolledText(f, height=12, state="disabled", font=("Consolas", 10), wrap="word")
        self.logbox.pack(fill="both", expand=True)
        self.logbox.tag_config("head", font=("Consolas", 10, "bold"))
        self.logbox.tag_config("err", foreground="#c0392b")
        self.logbox.tag_config("ok", foreground="#1e8449")

        bot = ttk.Frame(f)
        bot.pack(fill="x", pady=(8, 0))
        ttk.Label(bot, textvariable=self.cookie_var).pack(side="left")
        ttk.Button(bot, text="Clear log", command=self.clear_log).pack(side="right")

    # ---------- settings ----------
    def build_settings(self):
        f = self.tab_set
        self.ent = {}
        rows = [("YouTube channel ID (UC...)", "youtube", "channel_id", False),
                ("Telegram bot token", "telegram", "token", True),
                ("Telegram chat ID", "telegram", "chat_id", False),
                ("Only videos with this word in the title", "youtube", "title_filter", False),
                ("How many recent videos to check", "youtube", "latest", False),
                ("File name prefix", "video", "prefix", False),
                ("Delete finished videos older than (days, 0 = never)", "video", "cleanup_days", False),
                ("Daily summary on Telegram at (HH:MM, empty = never)", "app", "summary_time", False)]
        for i, (label, sec, key, secret) in enumerate(rows):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky="w", pady=6)
            e = ttk.Entry(f, width=40, show="*" if secret else "")
            e.insert(0, self.cfg.get(sec, key))
            e.grid(row=i, column=1, padx=12, sticky="we")
            self.ent[(sec, key)] = e
        n = len(rows)
        ttk.Label(f, text="Where to read the YouTube cookies from").grid(row=n, column=0, sticky="w", pady=6)
        self.cookie_src = tk.StringVar(value=key_of(COOKIE_SRC, self.cfg.get("youtube", "cookie_source"), "cookies.txt file"))
        ttk.Combobox(f, state="readonly", width=37, values=list(COOKIE_SRC), textvariable=self.cookie_src).grid(
            row=n, column=1, padx=12, sticky="w")
        n += 1
        self.unl_var = tk.BooleanVar(value=pl.flag(self.cfg, "youtube", "make_unlisted"))
        yt = ttk.LabelFrame(f, text=" Official YouTube access (recommended: no more cookies) ", padding=8)
        yt.grid(row=n, column=0, columnspan=2, sticky="we", pady=6)
        self.yt_var = tk.StringVar()
        ttk.Label(yt, textvariable=self.yt_var).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(yt, text="Make new private videos 'unlisted' (so they download without cookies)",
                        variable=self.unl_var).grid(row=1, column=0, columnspan=3, sticky="w", pady=4)
        self.del_var = tk.BooleanVar(value=pl.flag(self.cfg, "youtube", "delete_from_youtube"))
        ttk.Checkbutton(yt, text="Delete already-sent clips from YouTube after 7 days (CANNOT be undone)",
                        variable=self.del_var).grid(row=2, column=0, columnspan=3, sticky="w")
        ttk.Button(yt, text="Connect YouTube", command=self.connect_yt).grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Button(yt, text="Disconnect", command=self.disconnect_yt).grid(row=3, column=1, padx=8, sticky="w", pady=(6, 0))
        ttk.Button(yt, text="Step-by-step guide", command=self.open_guide).grid(row=3, column=2, sticky="w", pady=(6, 0))
        n += 1
        self.refresh_yt()
        f.columnconfigure(1, weight=1)
        btns = ttk.Frame(f)
        btns.grid(row=n, column=0, columnspan=2, sticky="w", pady=14)
        ttk.Button(btns, text="Save", command=self.save_settings).pack(side="left", ipady=4)
        ttk.Button(btns, text="Send test message", command=self.test_tg).pack(side="left", padx=8, ipady=4)
        ttk.Button(btns, text="Find chat ID", command=self.find_chat).pack(side="left", ipady=4)
        ttk.Separator(f).grid(row=n + 1, column=0, columnspan=2, sticky="we", pady=8)
        ttk.Label(f, justify="left", foreground="#444", text=(
            "YouTube cookies: to download private videos you need a cookies.txt file in the app folder.\n"
            "Export it from an incognito window (extension 'Get cookies.txt LOCALLY') and save it there.\n"
            "If downloads start failing, the cookies have expired: export them again.")
                  ).grid(row=n + 2, column=0, columnspan=2, sticky="w")
        ttk.Button(f, text="Open app folder", command=lambda: self.open_dir(pl.BASE)).grid(
            row=n + 3, column=0, sticky="w", pady=10)

    # ---------- actions ----------
    def open_dir(self, p):
        Path(p).mkdir(exist_ok=True)
        os.startfile(str(p))

    def toggle_boot(self):
        set_startup(self.boot_var.get())
        self.log("Start with Windows " + ("enabled.\n" if self.boot_var.get() else "disabled.\n"))

    def save_opts(self):
        styles = [s for s, v in (("blur", self.blur_var.get()), ("crop", self.crop_var.get())) if v] or ["blur"]
        if not (self.blur_var.get() or self.crop_var.get()):
            self.blur_var.set(True)
        yn = lambda b: "yes" if b else "no"
        v = {"styles": ",".join(styles), "audio": AUDIO.get(self.audio_var.get(), "original"),
             "length": str(LENGTH.get(self.length_var.get(), 59)), "slowmo": yn(self.slow_var.get()),
             "sharpen": yn(self.sharp_var.get()), "action_high": yn(self.high_var.get()),
             "watermark": self.wm_var.get().strip(),
             "multi_shorts": yn(self.multi_var.get()), "normalize": yn(self.norm_var.get()),
             "cover": yn(self.cov_var.get())}
        for k, val in v.items():
            self.cfg.set("video", k, val)
        try:
            self.cfg.set("app", "auto_minutes", str(max(2, int(self.min_var.get()))))
        except (tk.TclError, ValueError):
            pass
        pl.save_cfg(self.cfg)

    def save_settings(self, quiet=False):
        for (sec, key), e in self.ent.items():
            self.cfg.set(sec, key, e.get().strip())
        self.cfg.set("youtube", "cookie_source", COOKIE_SRC.get(self.cookie_src.get(), "file"))
        self.cfg.set("youtube", "make_unlisted", "yes" if self.unl_var.get() else "no")
        self.cfg.set("youtube", "delete_from_youtube", "yes" if self.del_var.get() else "no")
        pl.save_cfg(self.cfg)
        if not quiet:
            self.log("Settings saved.\n")

    def test_tg(self):
        self.save_settings(quiet=True)

        def job():
            ok = pl.telegram_text(pl.load_cfg(), "✅ Shorts Bot is connected correctly.")
            self.q.put("Test message sent.\n" if ok else "!! Test message failed: check the token and chat ID.\n")
        threading.Thread(target=job, daemon=True).start()

    def find_chat(self):
        self.save_settings(quiet=True)

        def job():
            with contextlib.redirect_stdout(Writer(self.q)):
                try:
                    pl.set_chat_id(pl.load_cfg())
                except Exception as e:
                    print("!!", e)
            self.q.put(("reload", None))
        threading.Thread(target=job, daemon=True).start()
        self.log("Looking for the chat ID (write 'hi' to the bot first)...\n")

    def refresh_yt(self):
        if yt_api.is_connected():
            self.yt_var.set("Status: YouTube connected.")
        elif yt_api.CLIENT_FILE.exists():
            self.yt_var.set("Status: not connected. Click 'Connect YouTube'.")
        else:
            self.yt_var.set("Status: not connected. client_secret.json is missing (follow the guide).")

    def open_guide(self):
        os.startfile(str(pl.BASE / "GUIDE_CONNECT_YOUTUBE.txt"))

    def connect_yt(self):
        if not yt_api.CLIENT_FILE.exists():
            messagebox.showinfo("Connect YouTube", "You need the client_secret.json file first: opening the guide.")
            self.open_guide()
            return
        self.save_settings(quiet=True)
        scope = yt_api.SCOPE_RW if self.unl_var.get() else yt_api.SCOPE_RO
        self.log("Connecting YouTube: the browser opens, choose the account and channel and click Allow.\n")

        def job():
            try:
                yt_api.authorize(scope=scope, log=lambda m: self.q.put(m + "\n"))
                self.q.put("YouTube connected successfully.\n")
                try:
                    n = len(yt_api.list_uploads(10))
                    self.q.put(f"Check passed: I can see {n} videos on the connected channel.\n")
                except Exception as e:
                    self.q.put(f"!! Connected, but I cannot read the videos: {e}\n")
            except Exception as e:
                self.q.put(f"!! Connection failed: {e}\n")
            self.q.put(("yt", None))
        threading.Thread(target=job, daemon=True).start()

    def disconnect_yt(self):
        yt_api.disconnect()
        self.refresh_yt()
        self.log("YouTube disconnected.\n")

    def refresh_cookies(self):
        src = COOKIE_SRC.get(self.cookie_src.get(), "file")
        if yt_api.is_connected():
            self.cookie_var.set("YouTube: connected with official access (cookies are only a backup)")
        elif src != "file":
            self.cookie_var.set(f"YouTube cookies: read from {self.cookie_src.get()}")
        elif pl.COOKIES.exists():
            t = time.strftime("%d/%m %H:%M", time.localtime(pl.COOKIES.stat().st_mtime))
            ok, missing = pl.cookie_status()
            if ok:
                self.cookie_var.set(f"YouTube cookies: complete (exported on {t})")
            else:
                self.cookie_var.set(f"YouTube cookies: INCOMPLETE, missing {', '.join(missing)} - export again from Studio")
        else:
            self.cookie_var.set("YouTube cookies: MISSING (see Settings)")
        self.after(5000, self.refresh_cookies)

    def check_all(self):
        self.save_settings(quiet=True)
        self.log("--- Checking everything (FFmpeg, YouTube, Telegram, disk) ---\n")

        def job():
            try:
                for ok, txt in pl.diagnostics(pl.load_cfg()):
                    self.q.put(("✔ " if ok else "✘ ") + txt + "\n")
            except Exception as e:
                self.q.put(f"!! Error during the check: {e}\n")
            self.q.put(("yt", None))
        threading.Thread(target=job, daemon=True).start()

    def cmd_tick(self):
        """In automatic mode, every 20 s checks whether you sent a command to the bot on Telegram."""
        def job():
            try:
                if pl.has_new_commands(pl.load_cfg()):
                    self.q.put(("cmd", None))
            except Exception:
                pass
        if self.auto_var.get() and not self.running:
            threading.Thread(target=job, daemon=True).start()
        self.after(20000, self.cmd_tick)

    def tick(self):
        """Every minute checks whether it is time for the daily summary (and runs the watchdog)."""
        try:
            mins = max(2, int(self.min_var.get()))
        except (tk.TclError, ValueError):
            mins = 10
        watch = self.auto_var.get() and time.time() - self.t0 > 90

        def job():
            cfg = pl.load_cfg()
            pl.maybe_summary(cfg)
            if watch:
                try:
                    pl.watchdog(cfg, mins)
                except Exception:
                    pass
        threading.Thread(target=job, daemon=True).start()
        self.after(60000, self.tick)

    # ---------- running ----------
    def busy(self, on, text=""):
        self.running = on
        for b in (self.btn, self.btn_best):
            b.state(["disabled"] if on else ["!disabled"])
        if on:
            self.status_var.set(text)

    def start(self):
        if self.running:
            return
        if self.after_id:
            self.after_cancel(self.after_id)
            self.after_id = None
        self.save_settings(quiet=True)
        self.save_opts()
        self.last_sent = 0
        self.stop_ev.clear()
        self.busy(True, "Working...")
        self.next_var.set("")
        self.log(f"--- Check {time.strftime('%H:%M:%S')} ---\n")
        threading.Thread(target=self.work, daemon=True).start()

    def work(self):
        try:
            with contextlib.redirect_stdout(Writer(self.q)), contextlib.redirect_stderr(Writer(self.q)):
                self.last_sent = pl.run_once(pl.load_cfg(), stop=self.stop_ev)
        except Exception as e:
            self.q.put(f"!! Error: {e}\n")
        finally:
            self.q.put(DONE)

    def best_of(self):
        if self.running:
            messagebox.showinfo("Best of", "Wait for the current job to finish.")
            return
        pl.OUT.mkdir(exist_ok=True)
        files = filedialog.askopenfilenames(title="Choose the clips to join (at least 2)", initialdir=str(pl.OUT),
                                            filetypes=[("Video", "*.mp4")])
        if len(files) < 2:
            if files:
                messagebox.showinfo("Best of", "Choose at least two.")
            return
        self.save_settings(quiet=True)
        self.save_opts()
        self.busy(True, "Creating the best of...")
        self.log(f"--- Best of ({len(files)} clips) ---\n")

        def job():
            try:
                with contextlib.redirect_stdout(Writer(self.q)), contextlib.redirect_stderr(Writer(self.q)):
                    pl.best_of_send(pl.load_cfg(), sorted(files))
            except Exception as e:
                self.q.put(f"!! Error: {e}\n")
            finally:
                self.q.put(DONE)
        threading.Thread(target=job, daemon=True).start()

    def toggle_auto(self):
        if self.after_id:
            self.after_cancel(self.after_id)
            self.after_id = None
        if self.auto_var.get():
            self.save_opts()
            if not self.running:
                self.start()
        else:
            self.next_var.set("")

    def schedule_next(self):
        if self.auto_var.get():
            m = max(2, int(self.min_var.get()))
            self.after_id = self.after(m * 60000, self.start)
            self.next_var.set("Next check at " + time.strftime("%H:%M", time.localtime(time.time() + m * 60)))

    def poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item is DONE:
                    self.busy(False)
                    self.status_var.set("Ready" + (f" - last clip sent at {time.strftime('%H:%M')}" if self.last_sent else ""))
                    self.schedule_next()
                elif isinstance(item, tuple) and item[0] == "cmd":
                    if not self.running:
                        self.log("Command received from Telegram.\n")
                        self.start()
                elif isinstance(item, tuple) and item[0] == "yt":
                    self.refresh_yt()
                    self.refresh_cookies()
                elif isinstance(item, tuple):
                    self.cfg = pl.load_cfg()
                    for (sec, key), e in self.ent.items():
                        e.delete(0, "end")
                        e.insert(0, self.cfg.get(sec, key))
                else:
                    self.log(item)
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def log(self, text):
        self.logbox.config(state="normal")
        tag = "head" if text.lstrip().startswith((">>", "---")) else "err" if "!!" in text or "ERROR" in text else \
            "ok" if "Sent" in text or "sent" in text else ""
        self.logbox.insert("end", text, tag)
        self.logbox.see("end")
        self.logbox.config(state="disabled")

    def clear_log(self):
        self.logbox.config(state="normal")
        self.logbox.delete("1.0", "end")
        self.logbox.config(state="disabled")

    def on_close(self):
        if self.running and not messagebox.askyesno("Quit", "It is still working. Close anyway?"):
            return
        self.destroy()


if __name__ == "__main__":
    if "--set-startup" in sys.argv:
        set_startup(sys.argv[sys.argv.index("--set-startup") + 1] == "on")
        print("Start with Windows set.")
    else:
        App().mainloop()
