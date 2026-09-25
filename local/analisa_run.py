#!/usr/bin/env python3
"""analisa_run.py — analisis hasil run farm dari state + DB 9router.

Fokus: berapa akun jadi, berapa gagal per kategori, apakah ada akun 'orphan'
(signup TH sukses tapi tidak tersimpan/inject), dan konsistensi conn di 9router.
"""
import json
import os
import re
import sqlite3
import sys
from collections import Counter

HERE = "/root/tokenharbor-farmer/local"
STATE = os.path.join(HERE, "th_state.json")
DB = "/root/project-ai/data/9router/db/data.sqlite"

st = json.load(open(STATE))
accs = st.get("accounts", [])
print(f"=== STATE ===")
print(f"  total akun      : {len(accs)}")
print(f"  verified        : {sum(1 for a in accs if a.get('verified'))}")
print(f"  consent         : {sum(1 for a in accs if a.get('consent'))}")
print(f"  injected        : {sum(1 for a in accs if a.get('injected'))}")
regions = Counter(a.get("region", "?") for a in accs)
print(f"  region          : {dict(regions)}")
print(f"  email domain    : {dict(Counter(a['email'].split('@')[1] for a in accs).most_common(6))}")

# urutkan per waktu
ts = [a for a in accs if a.get("ts")]
ts.sort(key=lambda a: a["ts"])
print(f"\n  akun terbaru (5):")
for a in ts[-5:]:
    print(f"    {a['ts'][:19]}  {a['email'][:36]}")

# === DB 9router ===
c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
rows = c.execute("SELECT id,name,email,data FROM providerConnections WHERE data LIKE '%tokenharbor.ai%'").fetchall()
print(f"\n=== 9ROUTER ===")
print(f"  conn TH total   : {len(rows)}")
keys_db = set()
for cid, name, email, data in rows:
    d = json.loads(data)
    keys_db.add(d.get("apiKey"))
labels = [r[1] for r in rows]
print(f"  label           : {labels[:5]} ... {labels[-3:]}")
dupe = [k for k, v in Counter(labels).items() if v > 1]
print(f"  label duplikat  : {dupe or 'tidak ada'}")

# === ORPHAN: akun di state tapi conn tidak ada, atau sebaliknya ===
keys_state = {a["api_key"] for a in accs if a.get("api_key")}
print(f"\n=== KONSISTENSI ===")
print(f"  key di state tapi TIDAK ada conn : {len(keys_state - keys_db)}")
print(f"  key di conn tapi TIDAK ada state : {len(keys_db - keys_state)}")

# conn tanpa email (conn lama user)
no_email = [r for r in rows if not r[2]]
print(f"  conn tanpa email (lama/manual)   : {len(no_email)} -> {[r[1] for r in no_email]}")

# === modelLock check ===
missing_lock = 0
for cid, name, email, data in rows:
    d = json.loads(data)
    if not any(k.startswith("modelLock_") for k in d):
        missing_lock += 1
print(f"  conn tanpa modelLock             : {missing_lock}")

# === testStatus ===
stat = Counter(json.loads(r[3]).get("testStatus") for r in rows)
print(f"  testStatus                      : {dict(stat)}")
c.close()