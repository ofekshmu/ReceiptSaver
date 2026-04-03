"""
ticktick_auth.py — Run ONCE to authorize TickTick.
1. Go to https://developer.ticktick.com/manage → Create app
2. Set redirect URI: http://localhost:8080
3. Fill CLIENT_ID and CLIENT_SECRET below
4. Run: python ticktick_auth.py
"""

import json, base64, urllib.parse, urllib.request, http.server, threading, webbrowser
from pathlib import Path
from secrets import TICKTICK_CLIENT_ID as CLIENT_ID, TICKTICK_CLIENT_SECRET as CLIENT_SECRET
REDIRECT_URI  = "http://localhost:8080"
TOKEN_FILE    = Path(__file__).parent / "ticktick_token.json"
auth_code     = None

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        auth_code = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("code", [None])[0]
        self.send_response(200); self.end_headers()
        self.wfile.write(b"<h2>Done! You can close this tab.</h2>")
    def log_message(self, *a): pass

def main():
    auth_url = "https://ticktick.com/oauth/authorize?" + urllib.parse.urlencode({
        "client_id": CLIENT_ID, "scope": "tasks:write tasks:read",
        "response_type": "code", "redirect_uri": REDIRECT_URI,
    })
    server = http.server.HTTPServer(("localhost", 8080), Handler)
    t = threading.Thread(target=server.handle_request); t.start()
    webbrowser.open(auth_url); t.join()
    if not auth_code: print("ERROR: no code received"); return
    data = urllib.parse.urlencode({"code": auth_code, "grant_type": "authorization_code",
                                   "redirect_uri": REDIRECT_URI}).encode()
    creds = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    req = urllib.request.Request("https://ticktick.com/oauth/token", data=data,
        headers={"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req) as r:
        TOKEN_FILE.write_text(json.dumps(json.loads(r.read()), indent=2), encoding="utf-8")
    print(f"✓ Saved to {TOKEN_FILE}")

if __name__ == "__main__": main()
