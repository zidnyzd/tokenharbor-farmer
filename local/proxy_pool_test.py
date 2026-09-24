#!/usr/bin/env python3
"""proxy_pool_test.py — uji pool proxy gaya grok-regkit untuk TokenHarbor.

Alur (sama seperti grok-regkit mode 'Whitelist Cliproxy' yang sebenarnya menukar
URL ke daftar monosans):
  1. Ambil daftar proxy dari repo GitHub (monosans/proxy-list, http.txt)
  2. Validasi tiap proxy via IPPure (my.ippure.com/v1/info) — filter HANYA fraudScore
     (tanpa syarat isResidential, sesuai permintaan: proxy datacenter boleh kalau skor rendah)
  3. Uji signup TokenHarbor melalui proxy yang lulus, pertama sukses = berhenti

Usage:
  python proxy_pool_test.py [--candidates 60] [--max-fraud 40] [--threads 20] [--signup-tries 15]
"""
import argparse
import concurrent.futures as cf
import json
import os
import random
import re
import string
import sys
import time
import urllib.parse
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests
import th_live as T

POOL_URL = "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt"
IPPURE = "https://my.ippure.com/v1/info"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "proxy_pool_result.json")


def log(m):
    print(f"  [{time.strftime('%H:%M:%S')}] {m}", flush=True)


def fetch_pool(url=POOL_URL):
    log(f"Ambil daftar proxy: {url}")
    t = requests.get(url, timeout=30).text
    items = [l.strip() for l in t.splitlines() if re.match(r"^\d+\.\d+\.\d+\.\d+:\d+$", l.strip())]
    log(f"  {len(items)} entri valid")
    return items


def check_one(hp, max_fraud):
    """Validasi satu proxy via ippure. Filter: fraudScore saja."""
    px = {"http": f"http://{hp}", "https": f"http://{hp}"}
    try:
        r = requests.get(IPPURE, proxies=px, timeout=20)
        d = r.json() or {}
        return {"hp": hp, "ip": d.get("ip"), "fraud": d.get("fraudScore"),
                "res": d.get("isResidential"), "country": d.get("country"),
                "isp": d.get("asOrganization"),
                "pass": isinstance(d.get("fraudScore"), (int, float)) and d["fraudScore"] <= max_fraud}
    except Exception as e:
        return {"hp": hp, "err": type(e).__name__, "pass": False}


def validate_pool(items, max_fraud, threads):
    log(f"Validasi {len(items)} proxy (fraudScore <= {max_fraud}, tanpa syarat residential)")
    out = []
    with cf.ThreadPoolExecutor(max_workers=threads) as ex:
        futs = {ex.submit(check_one, hp, max_fraud): hp for hp in items}
        for i, f in enumerate(cf.as_completed(futs), 1):
            r = f.result()
            out.append(r)
            if i % 10 == 0:
                ok = sum(1 for x in out if x["pass"])
                log(f"  {i}/{len(items)} dites, lulus: {ok}")
    out.sort(key=lambda x: (not x["pass"], x.get("fraud") if x.get("fraud") is not None else 999))
    return out


def try_signup_via(hp, thresh=0):
    """Coba 1 signup TH lewat proxy ini. Return dict hasil."""
    px = {"http": f"http://{hp}", "https": f"http://{hp}"}
    email, etok = T.gen_email()
    if not email:
        return {"hp": hp, "signed_in": False, "err": "tempmail gagal"}
    pwd = "".join(random.choices(string.ascii_letters + string.digits, k=12)) + "!Aa1"
    s = requests.Session()
    s.headers.update({"User-Agent": T.UA})
    try:
        h = s.get(f"{T.BASE}{T.SIGNUP_PATH}", proxies=px, timeout=60).text
        m = re.search(r'name="\$ACTION_1:0"[^>]*value="([^"]*)"', h)
        aid = json.loads(m.group(1).replace("&quot;", '"'))["id"]
        akey = re.search(r'name="\$ACTION_KEY"[^>]*value="([^"]*)"', h).group(1)
        body, hdr = T.make_body(aid, akey, email, pwd)
        r = s.post(f"{T.BASE}{T.SIGNUP_PATH}", data=body, headers=hdr, proxies=px, timeout=90)
        errs = [e for e in re.findall(r'"error":"([^"]+)"', r.text) if e not in ("$f", "$undefined", "$10")]
        ok = "signedIn" in r.text
        return {"hp": hp, "email": email, "signed_in": ok, "http": r.status_code,
                "err": (errs[0].encode().decode("utf-8", "replace")[:90] if errs else ""),
                "api_key": None}
    except Exception as e:
        return {"hp": hp, "signed_in": False, "err": f"{type(e).__name__}: {str(e)[:70]}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", type=int, default=60)
    ap.add_argument("--max-fraud", type=float, default=40)
    ap.add_argument("--threads", type=int, default=20)
    ap.add_argument("--signup-tries", type=int, default=15)
    ap.add_argument("--pool-url", default=POOL_URL, help="sumber daftar proxy lain")
    ap.add_argument("--no-signup", action="store_true", help="validasi saja, tanpa uji signup")
    a = ap.parse_args()

    print(f"=== UJI POOL PROXY (gaya grok-regkit: GitHub monosans + ippure) ===")
    print(f"    filter: fraudScore <= {a.max_fraud} | tanpa syarat residential\n")

    items = fetch_pool(a.pool_url)
    random.shuffle(items)
    cand = items[: a.candidates]

    res = validate_pool(cand, a.max_fraud, a.threads)
    passed = [x for x in res if x["pass"]]
    alive = [x for x in res if not x.get("err")]
    log(f"HASIL VALIDASI: {len(alive)} hidup, {len(passed)} lulus fraudScore<= {a.max_fraud}")
    for x in passed[:12]:
        log(f"  LULUS {x['hp']:22s} fraud={x['fraud']:>3} res={x['res']} {x.get('country','')} {str(x.get('isp'))[:28]}")
    for x in alive[:5]:
        if not x["pass"]:
            log(f"  tolak {x['hp']:22s} fraud={x.get('fraud')} res={x.get('res')}")

    json.dump({"validated": res, "passed": passed, "signup": []}, open(OUT, "w"), indent=2)

    if not passed or a.no_signup:
        log("Tidak ada proxy yang lulus / mode validasi saja. Uji signup dilewati.")
        print(json.dumps({"alive": len(alive), "passed": 0, "signup_tried": 0, "success": False}, indent=2))
        return

    log(f"\nUji signup TH lewat {min(len(passed), a.signup_tries)} proxy terbaik...")
    results = []
    for i, x in enumerate(passed[: a.signup_tries], 1):
        log(f"[{i}/{min(len(passed), a.signup_tries)}] {x['hp']} (fraud={x['fraud']}) — signup TH...")
        r = try_signup_via(x["hp"])
        r["fraud"] = x["fraud"]
        results.append(r)
        log(f"    → signedIn={r['signed_in']} {r.get('err','')}")
        json.dump({"validated": res, "passed": passed, "signup": results}, open(OUT, "w"), indent=2)
        if r["signed_in"]:
            log("    ✅ SIGNUP SUKSES lewat proxy publik — lanjut verify/consent/key/inject")
            break

    sukses = [r for r in results if r["signed_in"]]
    print(json.dumps({
        "alive": len(alive), "passed": len(passed),
        "signup_tried": len(results), "signup_success": len(sukses),
        "sukses_via": sukses[0]["hp"] if sukses else None,
        "hasil_file": OUT,
    }, indent=2))


if __name__ == "__main__":
    main()