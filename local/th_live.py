#!/usr/bin/env python3
"""th_live.py — TokenHarbor farmer FIXED for current TH build (Sep 2026).

Perbaikan atas repo Sekolah76/tokenharbor-farmer:
  1. [KRUSIAL] Next-Action header + $ACTION_1:0 id di-scrape LIVE dari
     /login?mode=signup (repo pakai ID/KEY lama yang sudah stale → HTTP 500).
  2. Action key juga live (bukan konstanta).
  3. Inject 9router memakai NODE TokenHarbor yang sudah ada
     (provider=openai-compatible-chat-<nodeid>, authType=apikey),
     TIDAK bikin node/conn duplikat 'openai-compatible'.
  4. Model free yang benar untuk build sekarang (2 model, lihat README model list
     dari instance sendiri via /v1/models bila perlu).
  5. Rotasi circuit NEWNYM + smart pause (dipertahankan dari repo).

Usage:
  python th_live.py single  [--no-inject] [--proxy URL | --direct | --tor] [--zira|--tempmail]
  python th_live.py farm N  [--no-inject] [--workers K] [--proxy URL | --direct]
  python th_live.py status
  python th_live.py cleanup        # buang akun unverified + conn-nya di 9router

Ketahanan (perbaikan 25 Sep 2026, setelah run `farm 20` mati di tengah):
  * req()  : semua request retry 3x dengan Sesi baru (IP baru) -> tidak crash
  * anti-orphan: akun + key dicatat ke state SEGERA setelah key terbit
  * email  : fallback otomatis tempmail.lol <-> mail.zira.web.id
  * inject : hanya untuk akun terverifikasi (key unverified = 403)
"""
import json, os, random, re, socket, sqlite3, string, sys, threading, time, urllib.parse, uuid
from datetime import datetime, timezone

import requests

BASE = "https://tokenharbor.ai"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
SIGNUP_PATH = "/login?mode=signup"
ROUTER = urllib.parse.quote('["",{"children":["login",{"children":["__PAGE__",{},null,null,0]},null,null,0]},null,null,20]')
FREE_MODELS = ["deepseek-v4-flash:free", "mimo-v2.5:free"]
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "th_state.json")
def _find_db():
    """DB 9router: env NINE_ROUTER_DB → lokasi umum (server/laptop/docker)."""
    env = os.environ.get("NINE_ROUTER_DB", "").strip()
    if env:
        return env
    for c in ("/root/project-ai/data/9router/db/data.sqlite",          # server ini (podman bind)
              os.path.expanduser("~/.9router/db/data.sqlite"),          # Linux/macOS
              os.path.expanduser("~/AppData/Roaming/9router/db/data.sqlite"),  # Windows
              "/app/data/data.sqlite"):                                 # docker
        if os.path.exists(c):
            return c
    return "/root/project-ai/data/9router/db/data.sqlite"


DB = _find_db()
SOCKS = {"http": "socks5h://127.0.0.1:9050", "https": "socks5h://127.0.0.1:9050"}
CTRL = ("127.0.0.1", 9051)
# MODE: "tor" (default repo) atau "direct" (IP server).
# TEMUAN 24 Sep 2026: SEMUA exit Tor ditolak TH ("We couldn't create your account"),
# signup dari IP server = 303 signedIn OK. Pakai `--direct` kecuali Tor pulih.
USE_TOR = True
# Proxy eksternal (di-set lewat --proxy / RESUMEPROXY). Menang atas USE_TOR.
PROXY = os.environ.get("TH_PROXY", "").strip()


def PX():
    if PROXY:
        return {"http": PROXY, "https": PROXY}
    return SOCKS if USE_TOR else None


def ip_quality(px):
    """Cek kualitas exit IP via ippure (isResidential / fraudScore).

    ippure bisa rate-limit (balas JSON kosong) → fallback ke ipify untuk IP-nya
    dan laporkan keterbatasan datanya, jangan tampilkan None-None tanpa penjelasan.
    """
    out = {}
    try:
        d = requests.get("https://my.ippure.com/v1/info", proxies=px, timeout=35).json() or {}
        out = {"ip": d.get("ip"), "country": d.get("country"),
               "residential": d.get("isResidential"), "fraud": d.get("fraudScore"),
               "isp": d.get("asOrganization")}
    except Exception as e:
        out = {"err": f"ippure: {str(e)[:50]}"}
    if not out.get("ip"):
        try:
            out["ip"] = requests.get("https://api.ipify.org", proxies=px, timeout=30).text.strip()
            out.setdefault("note", "kualitas IP tidak terbaca (ippure rate-limit) — IP saja")
        except Exception as e:
            out.setdefault("err", f"ipify: {str(e)[:50]}")
    return out

_lock = threading.Lock()


def log(msg, lv="INFO"):
    print(f"  [{datetime.now().strftime('%H:%M:%S')}] [{lv}] {msg}", flush=True)


def newnym():
    try:
        s = socket.create_connection(CTRL, timeout=5)
        s.sendall(b"AUTHENTICATE\r\n"); s.recv(100)
        s.sendall(b"SIGNAL NEWNYM\r\n"); r = s.recv(100).decode()
        s.close()
        return "OK" in r
    except Exception as e:
        log(f"newnym err: {str(e)[:40]}", "WARN")
        return False


def exit_ip():
    try:
        return requests.get("https://api.ipify.org", proxies=PX(), timeout=25).text.strip()
    except Exception:
        return "?"


def scrape_action(sess):
    """Ambil action id + key + action meta dari form signup LIVE."""
    h = sess.get(f"{BASE}{SIGNUP_PATH}", proxies=PX(), timeout=90).text

    def hv(name):
        m = re.search(r'name="' + re.escape(name) + r'"[^>]*value="([^"]*)"', h)
        if not m:  # kadang urutan atribut terbalik
            m = re.search(r'value="([^"]*)"[^>]*name="' + re.escape(name) + r'"', h)
        return (m.group(1) if m else "").replace("&quot;", '"')

    raw = hv("$ACTION_1:0")
    aid = json.loads(raw)["id"] if raw else None
    akey = hv("$ACTION_KEY")
    if not aid or not akey:
        raise RuntimeError("action id/key tidak ditemukan di halaman signup")
    return aid, akey


# Provider email: "tempmail" (default, pihak ketiga) atau "zira" (mail.zira.web.id sendiri).
# tempmail.lol kena rate-limit setelah ratusan inbox → pakai milik sendiri kalau perlu.
EMAIL_PROVIDER = os.environ.get("TH_EMAIL", "tempmail").strip().lower()


class NetError(Exception):
    """Kegagalan jaringan/proxy (transient) — bukan kesalahan logika."""


def req(method, url, sess=None, tries=3, pause=4, rotate=True, **kw):
    """Request tahan-error: retry dengan Sesi BARU (IP baru) bila kena error transient.

    Ini menutup lubang yang membuat run `farm 20` mati total: satu ReadTimeout
    pada POST /api/me/privacy melempar exception ke thread worker tanpa tertangkap.

    Return requests.Response, atau None kalau semua percobaan gagal (pemanggil
    memperlakukannya sebagai kegagalan akun biasa, bukan crash).
    """
    kw.setdefault("timeout", 40)
    last = None
    for i in range(tries):
        try:
            s = sess or requests.Session()
            if sess is None:
                s.headers.update({"User-Agent": UA})
            return s.request(method, url, proxies=PX(), **kw)
        except Exception as e:
            last = e
            transient = any(t in type(e).__name__ for t in
                            ("Timeout", "ConnectionError", "ProxyError", "SSLError"))
            log(f"  net retry {i+1}/{tries}: {type(e).__name__} {str(e)[:45]}", "WARN")
            if i < tries - 1:
                if rotate and not PROXY:
                    newnym()
                time.sleep(pause * (i + 1))
    raise NetError(f"{type(last).__name__}: {str(last)[:70]}")


def gen_email():
    """Ambil inbox baru. Return (address, token).

    Urutan provider: sesuai EMAIL_PROVIDER, lalu fallback otomatis ke provider
    lain kalau yang utama rate-limit/gagal (tempmail.lol -> mail.zira).
    """
    order = ["tempmail", "zira"] if EMAIL_PROVIDER == "tempmail" else ["zira", "tempmail"]
    for prov in order:
        if prov == "zira":
            try:
                import mail_zira as M
                addr, jwt = M.create_address()
                if addr:
                    if prov != EMAIL_PROVIDER:
                        log(f"  email fallback -> mail.zira ({addr[:26]})", "WARN")
                    return addr, jwt
            except Exception as e:
                log(f"  mail.zira err: {str(e)[:50]}", "WARN")
        else:
            for _ in range(2):
                try:
                    d = requests.post("https://api.tempmail.lol/v2/inbox/create", timeout=20).json()
                    if d.get("address") and d.get("token"):
                        return d["address"], d["token"]
                except Exception as e:
                    log(f"  tempmail err: {str(e)[:40]}", "WARN")
                time.sleep(3)
    return None, None


def _gen_email_old():
    """(arsip) implementasi lama — disimpan untuk referensi."""
    if EMAIL_PROVIDER == "zira":
        try:
            import mail_zira as M
            addr, tok = M.create_address()
            if addr:
                return addr, tok
        except Exception as e:
            log(f"mail.zira err: {str(e)[:50]}", "WARN")
        return None, None
    for _ in range(4):
        try:
            d = requests.post("https://api.tempmail.lol/v2/inbox/create", timeout=20).json()
            if d.get("address") and d.get("token"):
                return d["address"], d["token"]
        except Exception as e:
            log(f"tempmail err: {str(e)[:40]}", "WARN")
        time.sleep(3)
    return None, None


def make_body(aid, akey, email, pwd):
    fp = str(uuid.uuid4())
    bd = "----WebKitFormBoundary" + "".join(random.choices(string.ascii_uppercase + string.digits, k=16))
    parts = []

    def af(n, v=""):
        parts.append(f'--{bd}\r\nContent-Disposition: form-data; name="{n}"\r\n\r\n{v}')

    af("1_$ACTION_REF_1")
    af("1_$ACTION_1:0", json.dumps({"id": aid, "bound": "$@1"}))
    af("1_$ACTION_1:1", '["$undefined"]')
    af("1_$ACTION_KEY", akey)
    af("1_device_fingerprint", fp); af("1_timezone", "Asia/Jakarta"); af("1_next", "")
    af("1_email", email); af("1_password", pwd); af("1_invite_code", "")
    af("0", '["$undefined","$K1"]')
    body = "\r\n".join(parts) + f"\r\n--{bd}--\r\n"
    hdrs = {
        "Content-Type": f"multipart/form-data; boundary={bd}",
        "Accept": "text/x-component",
        "Next-Action": aid,                      # <-- INI kunci perbaikannya
        "Next-Router-State-Tree": ROUTER,
        "Origin": BASE,
        "Referer": f"{BASE}{SIGNUP_PATH}",
    }
    return body, hdrs


def verify_email(etok, max_wait=180, address=None, provider=None):
    """Tunggu link verifikasi lalu klik. `etok` = token tempmail ATAU jwt mail.zira."""
    prov = provider or EMAIL_PROVIDER
    if prov == "zira" or (etok and etok.count(".") == 2 and not address):
        # mail.zira: pakai jwt
        try:
            import mail_zira as M
            return bool(M.find_verify_link(etok, max_wait=max_wait, proxies=PX()))
        except Exception as e:
            log(f"verify via mail.zira err: {str(e)[:50]}", "WARN")
            return False
    start = time.time()
    while time.time() - start < max_wait:
        try:
            r = requests.get(f"https://api.tempmail.lol/v2/inbox?token={etok}", timeout=20)
            for em in r.json().get("emails", []):
                links = re.findall(r'(https://tokenharbor\.ai/verify-email\?[^\s"<>\\]+)', em.get("body", ""))
                if not links:
                    links = re.findall(r'(https://[^\s"<>\\]*verify[^\s"<>\\]*)', em.get("body", ""))
                if links:
                    url = links[0].replace("&amp;", "&")
                    for px in (PX(), None):
                        try:
                            requests.get(url, timeout=30, proxies=px, allow_redirects=True)
                            return True
                        except Exception:
                            continue
                    return True
        except Exception:
            pass
        time.sleep(3)
    return False


def register_one(use_tor=True, email_provider=None):
    """Satu siklus pendaftaran.

    PENTING (perbaikan): begitu akun dibuat + API key terbit, akun LANGSUNG
    dicatat ke state (pending) SEBELUM langkah consent/verify. Sebelumnya state
    disimpan di akhir, sehingga crash pada POST /api/me/privacy membuang akun
    yang sudah jadi di sisi TokenHarbor (kasus consolata47ef3).
    """
    email, etok = gen_email()
    if not email:
        return None, "email gagal (tempmail + mail.zira)"
    pwd = "".join(random.choices(string.ascii_letters + string.digits, k=12)) + "!Aa1"
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    try:
        aid, akey = scrape_action(s)
    except Exception as e:
        return None, f"scrape gagal: {str(e)[:60]}"
    body, hdrs = make_body(aid, akey, email, pwd)
    r = None
    for a in range(3):
        try:
            r = s.post(f"{BASE}{SIGNUP_PATH}", data=body, headers=hdrs, proxies=PX(), timeout=90)
            break
        except Exception as e:
            log(f"signup retry {a+1}: {str(e)[:40]}", "WARN")
            time.sleep(5)
    if r is None:
        return None, "tor timeout"
    if "signedIn" not in r.text:
        errs = [e for e in re.findall(r'"error":"([^"]+)"', r.text) if e not in ("$f", "$undefined")]
        err = errs[0] if errs else f"HTTP {r.status_code}"
        low = err.lower()
        if any(k in low for k in ("couldn't create", "human check", "too many", "captcha")):
            log("  IP exit buruk/blok → rotate", "WARN")
            newnym(); time.sleep(8)
        return None, f"signup: {err[:90]}"
    uid = re.findall(r'"userId":\s*"([^"]+)"', r.text)
    log(f"  Signup OK — {email} (uid {uid[0][:8] if uid else '?'})")
    # 1 akun = 1 key: hapus key auto
    try:
        r2 = s.get(f"{BASE}/api/keys", headers={"Accept": "application/json"}, proxies=PX(), timeout=40)
        for k in r2.json().get("keys", []):
            try:
                s.delete(f"{BASE}/api/keys/{k['id']}", proxies=PX(), timeout=25)
            except Exception:
                pass
    except Exception:
        pass
    try:
        r3 = req("POST", f"{BASE}/api/keys", sess=s, tries=3,
                 json={"label": f"th-{random.randint(1000,9999)}"},
                 headers={"Accept": "application/json", "Content-Type": "application/json"},
                 timeout=60)
    except NetError as e:
        return None, f"key create net error: {str(e)[:50]}"
    if r3 is None or r3.status_code != 201:
        return None, f"key create {getattr(r3, 'status_code', 'no-resp')}"
    key = (r3.json() or {}).get("plaintext")
    if not key:
        return None, "no plaintext"
    log(f"  Key: {key[:28]}…")
    # --- ANTI-ORPHAN: catat akun + key SEKARANG, sebelum langkah rawan ---
    rec = {"email": email, "password": pwd, "userId": uid[0] if uid else "",
           "api_key": key, "verified": False, "consent": False,
           "state": "key_created", "injected": False,
           "ts": datetime.now(timezone.utc).isoformat()}
    _upsert_account(rec)
    log("  akun dicatat (pending) — aman kalau langkah berikutnya gagal")

    # consent (jangan sampai timeout mematikan worker)
    consent = False
    try:
        rc = req("POST", f"{BASE}/api/me/privacy", sess=s, tries=3,
                 json={"free_models_enabled": True},
                 headers={"Accept": "application/json", "Content-Type": "application/json"},
                 timeout=40)
        if rc is not None:
            consent = rc.status_code == 200 and '"ok":true' in rc.text
            log(f"  Free models consent: {'Y' if consent else 'N'} ({rc.status_code})")
    except Exception as e:
        log(f"  consent gagal (dilewati): {str(e)[:50]}", "WARN")
    log("  Verify email…")
    try:
        verified = verify_email(etok, address=email, provider=email_provider)
    except Exception as e:
        log(f"  verify error: {str(e)[:50]}", "WARN")
        verified = False
    log(f"  Verified: {'Y' if verified else 'N'}")
    rec.update({"verified": verified, "consent": consent,
                "state": "verified" if verified else "unverified"})
    _upsert_account(rec)
    return rec, None


def test_model(key, model=None):
    for m in ([model] if model else FREE_MODELS):
        try:
            r = requests.post(f"{BASE}/v1/chat/completions",
                              headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                              timeout=90,
                              json={"model": m, "messages": [{"role": "user", "content": "say ok"}], "max_tokens": 20})
            if r.status_code == 200:
                return True, f"{m} 200"
            last = f"{m} {r.status_code}"
        except Exception as e:
            last = f"{m} ERR {str(e)[:30]}"
    return False, last


def resolve_th_node(cur):
    row = cur.execute("SELECT id FROM providerNodes WHERE data LIKE '%tokenharbor.ai%' OR name LIKE '%harbor%' LIMIT 1").fetchone()
    if row:
        return row[0]
    row = cur.execute("SELECT provider FROM providerConnections WHERE data LIKE '%tokenharbor.ai%' AND provider LIKE 'openai-compatible-chat-%' LIMIT 1").fetchone()
    if row:
        return row[0]
    nid = "openai-compatible-chat-" + str(uuid.uuid4())
    cur.execute("INSERT INTO providerNodes (id,name,data) VALUES (?,?,?)",
                (nid, "TokenHarbor", json.dumps({"prefix": "th", "apiType": "chat", "baseUrl": "https://tokenharbor.ai/v1"})))
    return nid


def inject(api_key, email):
    try:
        c = sqlite3.connect(DB, timeout=30)
        cur = c.cursor()
        provider = resolve_th_node(cur)
        n = cur.execute("SELECT COUNT(*) FROM providerConnections WHERE data LIKE '%tokenharbor.ai%'").fetchone()[0]
        label = f"TH th{n+1:03d}"
        now = datetime.now(timezone.utc).isoformat()
        d = {"apiKey": api_key, "label": label, "defaultModel": "deepseek-v4-flash:free",
             "testStatus": "active",
             "providerSpecificData": {"prefix": "th", "apiType": "chat",
                                      "baseUrl": "https://tokenharbor.ai/v1", "nodeName": "TokenHarbor"}}
        for m in FREE_MODELS:
            d[f"modelLock_{m}"] = 1
        cur.execute("INSERT INTO providerConnections (id,provider,authType,name,email,priority,isActive,data,createdAt,updatedAt) "
                    "VALUES (?,?,'apikey',?,?,0,1,?,?,?)",
                    ('conn-' + str(uuid.uuid4()), provider, label, email, json.dumps(d), now, now))
        c.commit(); c.close()
        return True, f"{label} → node {provider[:34]}"
    except Exception as e:
        return False, str(e)[:80]


def _upsert_account(rec):
    """Tambah/perbarui akun di state berdasarkan email (atomik via lock)."""
    with _lock:
        st = {"accounts": []}
        if os.path.exists(STATE):
            try:
                st = json.load(open(STATE))
            except Exception:
                st = {"accounts": []}
        accs = st.setdefault("accounts", [])
        for i, a in enumerate(accs):
            if a.get("email") == rec.get("email"):
                accs[i] = {**a, **rec}
                break
        else:
            accs.append(rec)
        tmp = STATE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(st, f, indent=2)
        os.replace(tmp, STATE)
    return rec


def load_state():
    if os.path.exists(STATE):
        return json.load(open(STATE))
    return {"accounts": []}


def save_state(st):
    with _lock:
        json.dump(st, open(STATE, "w"), indent=2)


def one_cycle(inject_on=True, email_provider=None):
    try:
        acct, err = register_one(email_provider=email_provider)
    except NetError as e:
        return None, f"net: {str(e)[:70]}"
    except Exception as e:
        # JANGAN biarkan satu akun mematikan seluruh run
        return None, f"{type(e).__name__}: {str(e)[:70]}"
    if not acct:
        return None, err
    try:
        ok, info = test_model(acct["api_key"])
        acct["test"] = info
        log(f"  Test model: {'OK' if ok else 'FAIL'} — {info}")
    except Exception as e:
        log(f"  test model error: {str(e)[:50]}", "WARN")
    # Inject HANYA kalau email terverifikasi (key unverified -> 403 di routing)
    if inject_on and acct.get("verified"):
        try:
            inj, msg = inject(acct["api_key"], acct["email"])
            acct["injected"] = inj
            log(f"  Inject 9router: {'OK' if inj else 'FAIL'} — {msg}")
        except Exception as e:
            acct["injected"] = False
            log(f"  inject error: {str(e)[:60]}", "WARN")
    elif inject_on:
        log("  Inject dilewati: email belum terverifikasi (key akan 403)", "WARN")
    _upsert_account(acct)
    return acct, None


def cmd_single(inject_on):
    tag = "proxy" if PROXY else ("tor" if USE_TOR else "direct")
    q = ip_quality(PX())
    print(f"=== SINGLE ({tag}, inject={'ON' if inject_on else 'OFF'}) ip={q.get('ip')} "
          f"residential={q.get('residential')} fraud={q.get('fraud')} {q.get('country') or ''} ===")
    acct, err = one_cycle(inject_on)
    if not acct:
        print("GAGAL:", err); return 1
    print(json.dumps({k: (v[:20] + "…" if k == "api_key" else v) for k, v in acct.items()}, indent=2, ensure_ascii=False))
    return 0


def cmd_farm(n, inject_on, workers=1):
    print(f"=== FARM target={n} workers={workers} inject={'ON' if inject_on else 'OFF'} ===")
    st = load_state()
    exist = {a.get("email") for a in st["accounts"]}
    done = {"n": len(st["accounts"]), "fail": 0}
    target = len(st["accounts"]) + n
    lock = threading.Lock()
    stop = threading.Event()

    def worker(wid):
        fails = 0
        mail_fails = 0
        provider = EMAIL_PROVIDER
        while not stop.is_set():
            with lock:
                if done["n"] >= target:
                    return
            try:
                acct, err = one_cycle(inject_on, email_provider=provider)
            except Exception as e:
                acct, err = None, f"{type(e).__name__}: {str(e)[:70]}"
            if acct:
                with lock:
                    done["n"] += 1
                    print(f"  ✅ [{done['n']}/{target}] {acct['email']} v:{'Y' if acct['verified'] else 'N'} "
                          f"inj:{'Y' if acct.get('injected') else 'N'} test:{acct.get('test')} (w{wid})", flush=True)
                fails = 0
                mail_fails = 0
                time.sleep(random.randint(3, 8))
            else:
                fails += 1
                log(f"[w{wid}] gagal: {err}", "ERROR")
                # deteksi rate-limit email -> langsung ganti provider (jangan pause 120s)
                if "email gagal" in (err or ""):
                    mail_fails += 1
                    if mail_fails >= 3:
                        provider = "zira" if provider == "tempmail" else "tempmail"
                        log(f"[w{wid}] email provider bermasalah {mail_fails}x "
                            f"-> ganti ke {provider}", "WARN")
                        mail_fails = 0
                        time.sleep(5)
                        continue
                else:
                    mail_fails = 0
                if not PROXY:
                    newnym()
                time.sleep(random.randint(5, 12))
                if fails >= 5:
                    log(f"[w{wid}] 5 gagal berturut — pause 60s + rotate", "WARN")
                    if not PROXY:
                        newnym()
                    time.sleep(60); fails = 0

    threads = [threading.Thread(target=worker, args=(i + 1,), daemon=True) for i in range(workers)]
    for t in threads:
        t.start(); time.sleep(1)
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(5)
    except KeyboardInterrupt:
        stop.set()
        log("Interrupt — stop", "WARN")
    stop.set()
    for t in threads:
        t.join(timeout=10)
    final = load_state()["accounts"]
    n_ver = sum(1 for a in final if a.get("verified"))
    n_inj = sum(1 for a in final if a.get("injected"))
    n_unver = sum(1 for a in final if not a.get("verified"))
    print(f"=== SELESAI: total {len(final)} akun di state ===")
    print(f"    verified: {n_ver} | injected: {n_inj} | belum verified: {n_unver}")
    if n_unver:
        print(f"    jalankan: ./venv/bin/python local/th_live.py cleanup   (buang akun unverified)")
    return 0


def cmd_status():
    st = load_state()
    acc = st.get("accounts", [])
    print(f"Akun di state      : {len(acc)}")
    print(f"  verified email   : {sum(1 for a in acc if a.get('verified'))}")
    print(f"  consent free     : {sum(1 for a in acc if a.get('consent'))}")
    print(f"  injected 9router : {sum(1 for a in acc if a.get('injected'))}")
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    n = c.execute("SELECT COUNT(*) FROM providerConnections WHERE data LIKE '%tokenharbor.ai%'").fetchone()[0]
    print(f"conn TH di 9router : {n}")
    print(f"node provider      : {resolve_th_node(c.cursor())[:60]}")
    c.close()
    return 0


def cmd_cleanup():
    """Buang akun yang belum terverifikasi dari state + hapus conn-nya di 9router."""
    st = load_state()
    accs = st.get("accounts", [])
    unver = [a for a in accs if not a.get("verified")]
    if not unver:
        print("Tidak ada akun unverified. Bersih.")
        return 0
    keys = {a.get("api_key") for a in unver}
    print(f"Akun unverified: {len(unver)}")
    for a in unver:
        print(f"  - {a.get('email')} ({a.get('state', '?')})")
    try:
        c = sqlite3.connect(DB, timeout=30)
        cur = c.cursor()
        rows = cur.execute("SELECT id,name,data FROM providerConnections "
                           "WHERE data LIKE '%tokenharbor.ai%'").fetchall()
        hapus = []
        for cid, name, data in rows:
            try:
                if json.loads(data).get("apiKey") in keys:
                    cur.execute("DELETE FROM providerConnections WHERE id=?", (cid,))
                    hapus.append(name)
            except Exception:
                pass
        c.commit(); c.close()
        print(f"conn dihapus di 9router: {len(hapus)} -> {hapus}")
    except Exception as e:
        print(f"gagal hapus conn: {str(e)[:70]}")
    st["accounts"] = [a for a in accs if a.get("verified")]
    save_state(st)
    print(f"state dibersihkan: {len(accs)} -> {len(st['accounts'])}")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    inj = "--no-inject" not in args
    if "--direct" in args:
        USE_TOR = False
    elif "--tor" in args:
        USE_TOR = True
    if "--proxy" in args:
        i = args.index("--proxy")
        if i + 1 < len(args):
            globals()["PROXY"] = args[i + 1].strip()
    if "--zira" in args:
        globals()["EMAIL_PROVIDER"] = "zira"
    elif "--tempmail" in args:
        globals()["EMAIL_PROVIDER"] = "tempmail"
    if not args or args[0] == "status":
        sys.exit(cmd_status())
    if args[0] == "cleanup":
        sys.exit(cmd_cleanup())
    if args[0] == "single":
        sys.exit(cmd_single(inj))
    if args[0] == "farm":
        n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 5
        w = 1
        if "--workers" in args:
            i = args.index("--workers")
            if i + 1 < len(args) and args[i + 1].isdigit():
                w = int(args[i + 1])
        sys.exit(cmd_farm(n, inj, w))
    print(__doc__)
