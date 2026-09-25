#!/usr/bin/env python3
"""mail_zira.py — adapter email pakai temp-mail SENDIRI (mail.zira.web.id).

Dipakai sebagai fallback saat api.tempmail.lol kena rate-limit.

API (hasil probe 25 Sep 2026, TERVERIFIKASI):
  POST /api/new_address            -> {"jwt": "...", "address": "..."}
  GET  /api/mails?limit=N&offset=0 -> {"results": [...], "count": n}
       Headers: Authorization: Bearer <jwt>   <-- WAJIB
       TIDAK pakai param `address` (address diambil server dari JWT).
       `limit` + `offset` keduanya wajib, kalau tidak: "Invalid limit"/"Invalid offset".

Catatan: versi pertama adapter ini salah (pakai param address, tanpa Bearer) →
selalu balas "Invalid address credential" dan verifikasi email gagal.
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests

BASE = "https://mail.zira.web.id"
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TIMEOUT = 25


def create_address(name=None, domain=None):
    """Bikin inbox baru. Return (address, jwt) atau (None, None)."""
    payload = {}
    if name:
        payload["name"] = name
    if domain:
        payload["domain"] = domain
    try:
        r = requests.post(f"{BASE}/api/new_address", json=payload, timeout=DEFAULT_TIMEOUT)
        if r.status_code >= 400:
            return None, None
        d = r.json() or {}
        return d.get("address"), d.get("jwt")
    except Exception as e:
        print(f"  [mail.zira] create gagal: {type(e).__name__} {str(e)[:60]}", file=sys.stderr)
        return None, None


def fetch_mails(jwt, limit=20, offset=0):
    """Ambil pesan masuk. WAJIB pakai Bearer jwt; limit+offset wajib."""
    if not jwt:
        return []
    for attempt in range(2):
        try:
            r = requests.get(f"{BASE}/api/mails",
                             params={"limit": limit, "offset": offset},
                             headers={"Authorization": f"Bearer {jwt}"},
                             timeout=DEFAULT_TIMEOUT)
            if r.status_code >= 400:
                return []
            d = r.json() or {}
            return d.get("results") or []
        except Exception:
            time.sleep(2)
    return []


def _body(m):
    """Gabungkan semua field yang mungkin memuat isi email."""
    parts = []
    for k in ("raw", "text", "html", "body", "content", "preview"):
        v = m.get(k)
        if isinstance(v, str):
            parts.append(v)
    return "\n".join(parts)


def extract_links(mails, pattern=r"https://tokenharbor\.ai/verify-email\?[^\s\"<>\\]+"):
    """Cari link verifikasi dari daftar pesan."""
    out = []
    for m in mails:
        body = _body(m)
        for link in re.findall(pattern, body):
            out.append(link.replace("&amp;", "&"))
        if not out:
            for link in re.findall(r"https://[^\s\"<>\\]*verify[^\s\"<>\\]*", body):
                out.append(link.replace("&amp;", "&"))
    return out


def find_verify_link(jwt, max_wait=180, pattern=r"https://tokenharbor\.ai/verify-email\?[^\s\"<>\\]+",
                     proxies=None):
    """Poll inbox sampai link verifikasi muncul, lalu GET link itu."""
    start = time.time()
    while time.time() - start < max_wait:
        links = extract_links(fetch_mails(jwt), pattern)
        if links:
            for url in links[:2]:
                for px in ([proxies, None] if proxies else [None]):
                    try:
                        requests.get(url, timeout=30, proxies=px, allow_redirects=True)
                        return True
                    except Exception:
                        continue
            return True
        time.sleep(5)
    return False


def selftest():
    print(f"=== selftest {BASE} ===")
    addr, jwt = create_address()
    print(f"  alamat: {addr}")
    if not addr:
        return 1
    print(f"  jwt   : {'ada' if jwt else 'TIDAK ADA'}")
    ms = fetch_mails(jwt)
    print(f"  mails : {len(ms)} pesan (API + Bearer OK)")
    return 0


if __name__ == "__main__":
    sys.exit(selftest())