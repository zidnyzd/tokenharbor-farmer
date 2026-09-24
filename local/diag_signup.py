#!/usr/bin/env python3
"""diag_signup.py — coba signup N kali dengan rotasi circuit, laporkan error per attempt.
Berguna untuk memisahkan: (a) exit node buruk vs (b) domain email ditolak / server error.
"""
import json, random, re, sys, time
sys.path.insert(0, "/root/tokenharbor-farmer/local")
import th_live as T
import requests

N = int(sys.argv[1]) if len(sys.argv) > 1 else 6
out = []
for i in range(1, N + 1):
    T.newnym(); time.sleep(10)
    ip = T.exit_ip()
    email, etok = T.gen_email()
    dom = email.split("@")[-1] if email else "?"
    pwd = "Aa1!" + T.uuid.uuid4().hex[:10]
    s = requests.Session(); s.headers.update({"User-Agent": T.UA})
    try:
        aid, akey = T.scrape_action(s)
        body, hdr = T.make_body(aid, akey, email, pwd)
        r = s.post(f"{T.BASE}{T.SIGNUP_PATH}", data=body, headers=hdr, proxies=T.SOCKS, timeout=90)
        errs = [e for e in re.findall(r'"error":"([^"]+)"', r.text) if e not in ("$f", "$undefined")]
        ok = "signedIn" in r.text
        rec = {"attempt": i, "ip": ip, "domain": dom, "http": r.status_code,
               "signed_in": ok, "error": errs[0][:110] if errs else ""}
    except Exception as e:
        rec = {"attempt": i, "ip": ip, "domain": dom, "http": "EXC", "signed_in": False,
               "error": f"{type(e).__name__}: {str(e)[:80]}"}
    out.append(rec)
    print(json.dumps(rec, ensure_ascii=False), flush=True)
    json.dump(out, open("/root/tokenharbor-farmer/local/diag_signup.json", "w"), indent=2)
    if rec["signed_in"]:
        print("SUKSES SIGNUP — pipeline terbukti end-to-end", flush=True)
        break
ok_n = sum(1 for r in out if r["signed_in"])
print(f"=== {ok_n}/{len(out)} sukses signup ===")