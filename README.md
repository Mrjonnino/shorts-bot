# Shorts Bot

**Record a clip on your PS5, go back to the couch, and find a ready-to-post vertical video on your phone.**

I play GTA 6 on PlayStation and I wanted my best moments to turn into TikToks and YouTube Shorts without
sitting at a computer. The PS5 can upload clips to YouTube, but the result is a wide, rough, 3-minute clip.
Shorts Bot sits on my Windows PC, picks up those clips by itself, trims the boring parts, reframes them
for a phone screen and sends them to me on Telegram. From there I add a trending song or a meme on my phone and post.

That's the whole idea. No editing software, no copying files around.

---

## How it works

```
 PS5  ──►  YouTube (private)  ──►  Shorts Bot on your PC  ──►  Telegram  ──►  your phone
 share      you upload nothing      downloads, trims,           the finished     add music,
 clip       by hand                 reframes to 1080x1920       video, as a      post it
                                                                 file (no
                                                                 recompression)
```

1. **You** upload a clip from the PS5 to your own YouTube channel (private is fine).
2. **The app** checks your channel every few minutes using YouTube's official API.
3. If the clip is private, it flips it to *unlisted* so it can be downloaded without any cookies.
4. It downloads the clip, **cuts the dead parts** (frozen frames, black screens), and if the clip is longer than
   a minute it keeps the most *alive* part: the one with the most movement and loudest audio.
5. It **reframes to vertical 1080x1920** (your gameplay on a blurred copy of itself, or cropped to fill the screen).
6. It sends the finished video to **your own Telegram bot**, as a *file* so Telegram doesn't squash the quality.
7. You add the song or the meme on your phone and post it. That last step is on purpose manual.

It never posts anything for you, and it never adds titles or hashtags. Those stay yours.

## What it can do

- **Auto-cut**: removes frozen/black sections, discards useless clips (too short, nothing happening).
- **Up to 59 seconds**, picked from the most active moment (video motion + audio loudness).
- **Several Shorts from one long clip**: up to 3 separate videos ("part 1/3" ...).
- **Slow-motion** on the peak moment (optional).
- **Two looks**: blurred background (shows everything) or full-screen crop. Or both.
- **Audio choices**: original, lowered (to put music on top), muted, or original + a muted copy.
- **Even volume**: loudness is normalized so your added music doesn't fight the game audio.
- **Cover image**: sends the strongest frame so you can use it as a thumbnail.
- **Sharpening** and a small **watermark** text (both optional).
- **Telegram remote control**: reply to a video with a short command and it re-edits it (see below).
- **Best-of**: join several finished clips into one video.
- **Health check** button and Telegram alerts if something stops working.
- **Daily summary** message, automatic cleanup of old files, optional start with Windows.
- Quality stays high (near-lossless encoding) while keeping every file under Telegram's 50 MB bot limit.

## Requirements

- Windows 10/11 (it uses `winget` and a Tkinter window; other systems are untested)
- A YouTube channel your PS5 uploads to
- A Telegram account
- A free Google Cloud project (a 10-minute one-time setup, explained below)

The installer takes care of **FFmpeg**, **Python**, **Deno** (YouTube needs it) and **yt-dlp**.

## Install

1. Download or clone this repository into a folder you like.
2. Double-click **`Install.bat`**. It installs what is missing (it may ask you to close the window and run it
   again once or twice), puts a *Shorts Bot* icon on your desktop and makes the app start with Windows.
3. Open the app, go to the **Settings** tab and follow the two setups below.

### 1) Telegram bot (2 minutes)

1. In Telegram, talk to **@BotFather**, send `/newbot`, pick a name, and copy the **token** it gives you.
2. Paste the token in the app's Settings.
3. Open your new bot in Telegram and send it any message (for example "hi").
4. In the app, click **Find chat ID**, then **Send test message**.

Treat the token like a password. Anyone with it can control your bot.

### 2) Connect YouTube (10 minutes, once)

This replaces the fragile "cookies" approach with the official login.

1. Go to <https://console.cloud.google.com> and sign in with the Google account that owns your channel.
2. Create a **new project** (any name).
3. **APIs & Services → Library** → find **YouTube Data API v3** → **Enable**.
4. **Google Auth Platform** (OAuth consent screen): choose **External**, fill in an app name and your email, accept the policy.
5. **Clients → Create client** → type **Desktop app** → create → **Download JSON**.
6. Rename that file to **`client_secret.json`** and put it in the app's folder.
7. In the app: **Settings → Connect YouTube**. Your browser opens; choose the channel and accept.
   You will see *"Google hasn't verified this app"*. That's expected: it is **your** app.
   Click **Advanced → Go to (your app) → Continue → Allow**.

Notes:
- While the Google project is in **Testing** mode, add your own account under *Audience → Test users*, and the login
  expires after 7 days (just click Connect again). If you publish the app to production, it doesn't expire.
  Publishing an unverified app for yourself only is fine, but Google asks for a homepage and a privacy-policy URL.
- The permission requested is `youtube` (manage your account) because changing a video from private to unlisted
  needs it. The app uses it only for that, and for the optional delete (off by default).
  If you turn off *"make private videos unlisted"* before connecting, it only asks for read access.
- **`client_secret.json` and `youtube_token.json` are secrets.** Never share them or commit them.

## Using it

- **Check now**: look for new clips right away.
- **Automatic mode**: checks every N minutes while the app is open. Tick *Start with Windows* and it just runs in the background.
- The options in the *Video* box (looks, audio, duration, slow-mo...) apply to the next clips.
- **Check everything**: a one-click health check (FFmpeg, yt-dlp, Deno, YouTube, Telegram, disk space).
- Only videos whose title contains the **title filter** are touched. Mine all start with `Grand Theft Auto VI_`,
  which is how the PS5 names its uploads, so my other videos are ignored. Change it in Settings.

### Telegram commands

Reply to a video the bot sent you (or just write to the bot; it uses the last clip). The original is kept for 3 days.

| You write | What happens |
|---|---|
| `short` / `medium` / `long` | re-edit at 20 / 40 / 59 seconds |
| `slow` / `noslow` | with / without slow-motion |
| `mute` | no audio |
| `crop` / `blur` | full-screen crop / blurred background |
| `redo` | edit it again |
| `status` | how many clips today |
| `check` | health check |
| `help` | list of commands |

You can combine them, for example `short slow crop`. The old Italian words (`corto`, `muto`, `rifai`, `stato`...) still work too.

## Safety and privacy

- Everything runs **on your own PC**. There is no server. The only outside services are YouTube (your own channel),
  Telegram (your own bot) and Google login.
- Secrets live only in files that are **git-ignored**: `config.ini`, `client_secret.json`, `youtube_token.json`, `cookies.txt`.
  Don't commit them. If one ever leaks (even for a minute, git remembers), **revoke it**: regenerate the bot token in
  @BotFather, delete the OAuth client in Google Cloud, and remove the app's access at <https://myaccount.google.com/permissions>.
- *Delete from YouTube after N days* is **off by default** and cannot be undone. Turn it on only if you mean it.
- The app only touches videos that match your title filter.

## Troubleshooting

- Start with **Check everything**. It names the problem in plain words.
- *"Only images are available" / JavaScript errors*: Deno is missing or yt-dlp is old. Run `Install.bat` again.
- *No new clips*: check the title filter; the log lists the videos it saw and the ones the filter excluded.
- *YouTube login expired*: Settings → **Connect YouTube** again (Testing mode expires after 7 days).
- Telegram only accepts files up to 50 MB from bots; the app re-encodes smaller automatically.
- The PC and the app must be on for it to work. If the PC was off, it catches up with missed clips when it starts again.

## Under the hood (for the curious)

| File | What it does |
|---|---|
| `autocut.py` | the video part: finds dead sections and the best moment, slow-mo, loudness, vertical render (FFmpeg) |
| `pipeline.py` | the brain: lists videos, downloads (yt-dlp), edits, sends to Telegram, commands, health check |
| `yt_api.py` | the official YouTube login and the few API calls it needs (no Google libraries, just `requests`) |
| `ShortsApp.pyw` | the window |
| `*.bat` | installer and shortcuts (`Install`, `Start_Bot`, `Set_Chat_ID`, `Make_Shorts`) |
| `GUIDE_CONNECT_YOUTUBE.txt` | the YouTube setup, step by step |

You can also use the editor alone: put videos in a folder `Clips_to_edit` and run `Make_Shorts.bat` (or `Make_Shorts_Crop.bat` for the full-screen look).

## Disclaimer

A personal hobby project, provided as is, with no warranty. Use it only with videos you own, and follow the terms of
YouTube, Telegram and your game. It is not affiliated with or endorsed by Sony, Rockstar Games, Take-Two, Google/YouTube or Telegram.
"GTA" and the other names belong to their owners.

## License

No license has been chosen yet. Until one is added, all rights are reserved by the author.
