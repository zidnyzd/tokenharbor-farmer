#!/usr/bin/env python3
"""cleanup_unverified.py — hapus conn TH yang key-nya belum terverifikasi (403).

Inject hanya boleh untuk akun yang lolos verifikasi; key unverified merusak
routing 9router (403 saat model dipakai).
"""
import json
import sqlite3
import sys

DB = "/root/project-ai/data/9router/db/data.sqlite"
STATE = "/root/tokenharbor-farmer/local/th_state.json"

state = json.load(open(STATE))
unver = {a["api_key"] for a in state["accounts"] if not a.get("verified")}
print(f"akun unverified di state: {len(unver)}")

c = sqlite3.connect(DB)
cur = c.cursor()
rows = cur.execute("SELECT id, name, data FROM providerConnections WHERE data LIKE '%tokenharbor.ai%'").fetchall()
hapus = []
for cid, name, data in rows:
    d = json.loads(data)
    if d.get("apiKey") in unver:
        hapus.append((cid, name))
        cur.execute("DELETE FROM providerConnections WHERE id=?", (cid,))
c.commit()
print(f"conn dihapus: {len(hapus)} -> {hapus}")
n = cur.execute("SELECT COUNT(*) FROM providerConnections WHERE data LIKE '%tokenharbor.ai%'").fetchone()[0]
print(f"conn TH tersisa: {n}")
c.close()