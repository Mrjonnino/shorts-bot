#!/usr/bin/env python3
"""Official YouTube access (Data API v3) with OAuth: lists your uploads, private ones included, and makes them 'unlisted'.
Needs the client_secret.json file (see the README, 'Connect YouTube'). No Google libraries needed, just requests."""
import base64, hashlib, json, secrets, time, webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
import requests

BASE = Path(__file__).resolve().parent
CLIENT_FILE = BASE / "client_secret.json"
TOKEN_FILE = BASE / "youtube_token.json"
SCOPE_RW = "https://www.googleapis.com/auth/youtube"
SCOPE_RO = "https://www.googleapis.com/auth/youtube.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"

_tok = {"access": None, "exp": 0.0}


class ApiError(Exception):
    pass


class AuthError(ApiError):
    pass


def is_connected():
    return TOKEN_FILE.exists() and CLIENT_FILE.exists()


def disconnect():
    _tok.update(access=None, exp=0.0)
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()


def load_client():
    if not CLIENT_FILE.exists():
        raise AuthError("client_secret.json is missing from the app folder (see the README, 'Connect YouTube').")
    try:
        d = json.loads(CLIENT_FILE.read_text(encoding="utf-8"))
    except ValueError:
        raise AuthError("client_secret.json is not a valid file.")
    c = d.get("installed") or d.get("web")
    if not c or "client_id" not in c or "client_secret" not in c:
        raise AuthError("client_secret.json is not usable: it must be an OAuth client of type 'Desktop app'.")
    return c


def authorize(scope=SCOPE_RW, log=print, timeout=300):
    """Opens the browser, lets you approve, and saves the connection (refresh token) in youtube_token.json."""
    c = load_client()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    result = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            q = parse_qs(urlparse(self.path).query)
            if "code" in q or "error" in q:
                result.update({k: v[0] for k, v in q.items()})
            body = ("<html><meta charset='utf-8'><body style='font-family:sans-serif;margin:3em'>"
                    "<h2>Done!</h2><p>You can close this tab and go back to Shorts Bot.</p></body></html>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{srv.server_address[1]}"
    url = c.get("auth_uri", AUTH_URL) + "?" + urlencode({
        "client_id": c["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": scope,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    log("Opening the browser: choose the account and channel, then click Allow...")
    webbrowser.open(url)
    srv.timeout = 1
    t0 = time.time()
    while not result and time.time() - t0 < timeout:
        srv.handle_request()
    srv.server_close()
    if result.get("error"):
        raise AuthError("Connection cancelled: " + result["error"])
    if "code" not in result or result.get("state") != state:
        raise AuthError("Connection not completed (timed out).")
    r = requests.post(c.get("token_uri", TOKEN_URL), data={
        "code": result["code"], "client_id": c["client_id"], "client_secret": c["client_secret"],
        "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier}, timeout=30)
    d = r.json() if r.content else {}
    if r.status_code != 200 or "refresh_token" not in d:
        raise AuthError("Google did not grant permission: " + str(d.get("error_description") or d.get("error") or r.status_code))
    TOKEN_FILE.write_text(json.dumps({"refresh_token": d["refresh_token"], "scope": scope}), encoding="utf-8")
    _tok.update(access=d.get("access_token"), exp=time.time() + int(d.get("expires_in", 0)))
    return True


def access_token():
    if _tok["access"] and time.time() < _tok["exp"] - 60:
        return _tok["access"]
    if not TOKEN_FILE.exists():
        raise AuthError("YouTube is not connected.")
    c = load_client()
    rt = json.loads(TOKEN_FILE.read_text(encoding="utf-8")).get("refresh_token")
    r = requests.post(c.get("token_uri", TOKEN_URL), data={
        "client_id": c["client_id"], "client_secret": c["client_secret"],
        "refresh_token": rt, "grant_type": "refresh_token"}, timeout=30)
    d = r.json() if r.content else {}
    if r.status_code != 200 or "access_token" not in d:
        if d.get("error") in ("invalid_grant", "unauthorized_client", "invalid_client"):
            raise AuthError("The YouTube connection has expired or was revoked: reconnect from Settings.")
        raise ApiError("Could not renew the connection: " + str(d.get("error_description") or d.get("error") or r.status_code))
    _tok.update(access=d["access_token"], exp=time.time() + int(d.get("expires_in", 3600)))
    return _tok["access"]


def _call(method, path, params=None, body=None):
    for attempt in (1, 2):
        r = requests.request(method, API + path, params=params, json=body, timeout=30,
                             headers={"Authorization": "Bearer " + access_token()})
        if r.status_code == 401 and attempt == 1:
            _tok.update(access=None, exp=0.0)       # token expired: renew and retry
            continue
        break
    d = r.json() if r.content else {}
    if not r.ok:
        err = d.get("error", {}) if isinstance(d, dict) else {}
        msg = err.get("message") or str(r.status_code)
        reasons = [e.get("reason") for e in err.get("errors", [])]
        if "quotaExceeded" in reasons:
            raise ApiError("Daily YouTube API quota reached: try again tomorrow.")
        if r.status_code == 403 and "insufficientPermissions" in reasons:
            raise AuthError("Insufficient permission: reconnect YouTube with the 'make private videos unlisted' option on.")
        raise ApiError(f"YouTube replied {r.status_code}: {msg}")
    return d


def list_uploads(n=10):
    """Latest uploads of the connected channel, private ones included: [(id, title, privacy)]."""
    ch = _call("GET", "/channels", {"part": "contentDetails", "mine": "true"})
    items = ch.get("items") or []
    if not items:
        raise ApiError("The connected account has no YouTube channel.")
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    pl = _call("GET", "/playlistItems", {"part": "snippet,status,contentDetails", "playlistId": uploads,
                                        "maxResults": max(1, min(50, int(n)))})
    out = []
    for it in pl.get("items", []):
        vid = (it.get("contentDetails") or {}).get("videoId") or it["snippet"]["resourceId"]["videoId"]
        out.append((vid, it["snippet"].get("title", vid), (it.get("status") or {}).get("privacyStatus", "")))
    return out


def make_unlisted(vid):
    """If the video is private, makes it 'unlisted' (visible only with the link). Returns True if it changed it."""
    cur = _call("GET", "/videos", {"part": "status", "id": vid}).get("items") or []
    if not cur:
        raise ApiError("Video not found on the connected account.")
    st = cur[0]["status"]
    if st.get("privacyStatus") != "private":
        return False
    new = {k: st[k] for k in ("embeddable", "license", "publicStatsViewable", "selfDeclaredMadeForKids") if k in st}
    new["privacyStatus"] = "unlisted"
    _call("PUT", "/videos", {"part": "status"}, {"id": vid, "status": new})
    return True


def delete_video(vid):
    """Permanently deletes a video from the connected channel (cannot be undone)."""
    _call("DELETE", "/videos", {"id": vid})
    return True
