#!/usr/bin/env python3
"""diag2.py — isolasi penyebab: coba signup via (a) IP server langsung, (b) Tor,
dengan ekstraksi error yang benar (baris flight `N:{"error":"..."}`).
"""
import json, random, re, sys, time
sys.path.insert(0, "/root/tokenharbor-farmer/local")
import th_live as T
import requests

def real_error(text):
    for m in re.finditer(r'\d+:\{"error":"((?:[^"\\]|\\.)*)"', text):
        v = m.group(1)
        if v not in ("$f", "$undefined", "$10"):
            return v.encode().decode("utf-8", "replace")[:160]
    return ""

def attempt(tag, use_tor, email=None):
    email, etok = T.gen_email() if email is None else (email, None)
    dom = email.split("@")[-1]
    pwd = "Aa1!" + T.uuid.uuid4().hex[:10]
    s = requests.Session(); s.headers.update({"User-Agent": T.UA})
    px = T.SOCKS if use_tor else None
    try:
        h = s.get(f"{T.BASE}{T.SIGNUP_PATH}", proxies=px, timeout=90).text
        m = re.search(r'name="\$ACTION_1:0"[^>]*value="([^"]*)"', h)
        aid = json.loads(m.group(1).replace("&quot;", '"'))["id"]
        akey = re.search(r'name="\$ACTION_KEY"[^>]*value="([^"]*)"', h).group(1)
        body, hdr = T.make_body(aid, akey, email, pwd)
        r = s.post(f"{T.BASE}{T.SIGNUP_PATH}", data=body, headers=hdr, proxies=px, timeout=90)
        ok = "signedIn" in r.text
        rec = {"tag": tag, "tor": use_tor, "ip": T.exit_ip() if use_tor else "DIRECT", "domain": dom,
               "http": r.status_code, "signedIn": ok, "err": real_error(r.text)}
    except Exception as e:
        rec = {"tag": tag, "tor": use_tor, "domain": dom, "http": "EXC",
               "err": f"{type(e).__name__}: {str(e)[:90]}"}
    print(json.dumps(rec, ensure_ascii=False), flush=True)
    return rec

res = []
# 1) direct (IP server) — hanya 1x, diagnostik
res.append(attempt("direct", False))
# 2) tor, 2 percobaan
for i in range(2):
    T.newnym(); time.sleep(10)
    res.append(attempt(f"tor-{i+1}", True))
# 3) model list publik TH (cek model free yang benar-benar tersedia)
try:
    r = requests.get(f"{T.BASE}/v1/models", timeout=40)
    print("GET /v1/models ->", r.status_code, r.text[:200])
except Exception as e:
    print("models err", str(e)[:80])
json.dump(res, open("/root/tokenharbor-farmer/local/diag2.json", "w"), indent=2)