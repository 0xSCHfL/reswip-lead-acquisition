"""Extract infobel.com cookies from Chrome and save as Playwright-compatible JSON."""
import json
import os
import shutil
import sqlite3
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# 1. Get Chrome encryption key from Local State
local_state = Path.home() / ".config/chromium/Local State"
if not local_state.exists():
    local_state = Path.home() / ".config/google-chrome/Default/Local State"

with open(local_state) as f:
    state = json.load(f)
enc_key_b64 = state["os_crypt"]["encrypted_key"]
enc_key = eval(enc_key_b64) if enc_key_b64.startswith("b'") else enc_key_b64
# It's base64 + "DPAPI" prefix
import base64
raw = base64.b64decode(enc_key_b64)
assert raw[:5] == b'DPAPI'
encrypted_key = raw[5:]

# Decrypt using pycryptodome or cryptography
# On Linux Chrome uses AES-GCM with key derived from DBUS secret service
# Actually recent Chrome uses OSCrypt which uses AES-256-GCM
# The key is encrypted with a random key stored by the OS keyring

# Let's try a different approach - just use Playwright to add cookies directly
# First, let me check if we can read cookies at all
import http.cookiejar

# Simpler option: just dump what we can from Chrome
cookie_db = Path.home() / ".config/chromium/Default/Cookies"
if not cookie_db.exists():
    cookie_db = Path.home() / ".config/google-chrome/Default/Cookies"

tmp = "/tmp/chrome_cookies.db"
shutil.copy2(cookie_db, tmp)
conn = sqlite3.connect(tmp)
conn.text_factory = bytes

# Get encrypted cookies for infobel
cursor = conn.cursor()
rows = cursor.execute(
    "SELECT host_key, name, encrypted_value, path, expires_utc, is_secure, is_httponly, has_httponly "
    "FROM cookies WHERE host_key LIKE '%infobel%'"
).fetchall()
conn.close()
os.unlink(tmp)

print(f"Found {len(rows)} infobel cookies (encrypted)")
for row in rows:
    host, name, val, path, expires, secure, httponly, has_httponly = row
    print(f"  {name} ({host}): {len(val)} bytes encrypted")

print("\nCan't decrypt Chrome cookies from CLI.")
print("Alternative: run with --headed and solve captcha manually in Playwright window.")
