# Perbaikan untuk instalasi di Linux (hasil audit 24 Sep 2026)

Repo upstream punya beberapa bagian yang **tidak berjalan apa adanya** — semuanya diverifikasi
dengan menjalankan kodenya, bukan dari membaca saja. Folder `local/` berisi versi perbaikan.

## Temuan (semua terbukti)

| # | Masalah | Bukti |
|---|---|---|
| 1 | **`Next-Action` header tidak dikirim** dan action ID/key di-hardcode sudah **stale** → signup selalu `HTTP 500` (`__next_error__`) | repo `6003703e…` / `kb59e6b8…`; halaman live: `6086b863…` = fungsi `signUp`, `6039ce3c…` = `signIn`, key `kcf78441…` |
| 2 | Rotasi Tor (`SIGNAL NEWNYM`) butuh `ControlPort` — repo hanya mendokumentasikan SocksPort | `/etc/tor/torrc` default tidak punya ControlPort |
| 3 | `inject_th_kv.py` memakai node/prefix **`tokenbor`** yang tidak ada | query `SELECT id FROM providerConnections WHERE provider='tokenbor'` → kosong |
| 4 | `fix_th_provider.py` memakai `authType='api_key'`, 9router memakai **`apikey`** | baris `TARGET_AUTH = "apikey"` vs conn existing |
| 5 | `th_farm_multi.py` butuh **`tor.exe`** (Windows-only) | `spawn_tor()` → `os.path.join(TOR_DIR, "tor.exe")` |
| 6 | `th_tui.py` menu "Test API key" → **`NameError: _has_test`** (variabel tidak pernah ada) | dijalankan langsung |
| 7 | `th_tui.py` menampilkan Tor `✅ READY` tanpa memeriksa apakah Tor benar-benar bisa dipakai | semua exit Tor ditolak TH |
| 8 | Inject bawaan memakai `provider='openai-compatible'` → **node duplikat**, bukan gabung ke node `TokenHarbor` | repo menyertakan `fix_th_provider.py` sebagai tambalan |
| 9 | `db_path.py` tidak menemukan DB pada 9router yang jalan di container (bind mount) | butuh env `NINE_ROUTER_DB` |

## Versi perbaikan (`local/`)

- **`local/th_live.py`** — adapter utama:
  - action ID + `$ACTION_KEY` **di-scrape live** dari `/login?mode=signup`, header `Next-Action` dikirim;
  - `--proxy <url>` / env `TH_PROXY` / `--tor` / `--direct`;
  - inject **gabung ke node TokenHarbor yang sudah ada** (`provider=openai-compatible-chat-…`, `authType=apikey`), `modelLock` + `prefix` + `baseUrl` benar;
  - `--no-inject`, retry + rotasi circuit, state `th_state.json` (resume), `status`.
- **`local/th_tui.py`** — TUI fungsional (Rich): status ringkas, list akun, **test key langsung ke TH (HIDUP/MATI)**, test rute `th/*` via 9router, cek jalur IP, register 1 akun, batch dengan jumlah custom (maks `MAX_BATCH`), export mode 600.
- **`local/diag_signup.py`, `local/diag2.py`** — diagnostik: memisahkan "kode salah" vs "jalur IP diblok".

## Catatan penting soal jalur IP (diukur, bukan asumsi)

| Jalur | Hasil |
|---|---|
| Exit Tor | ❌ ditolak TH: `We couldn't create your account` (6/6 exit berbeda) |
| IP datacenter (server) | ⚠️ ~1–2 akun lalu `Please complete the human check` |
| Proxy residensial/rotating | ✅ dipakai; tiap request IP baru |

Menambah akun secara massal = menerobos rate-limit + Turnstile. Free tier TH dibatasi
(catatan: 7 hari per akun sejak consent). Pakai sesuai ketentuan layanan.

## Setup cepat (Linux)

```bash
git clone <repo-fork>
cd tokenharbor-farmer
python3 -m venv venv && ./venv/bin/pip install requests pysocks rich
export NINE_ROUTER_DB=/path/ke/9router/data.sqlite
./venv/bin/python local/th_live.py single --direct     # 1 akun uji
./venv/bin/python local/th_tui.py --direct             # TUI
```

Tor (opsional, kalau mau rotasi circuit): tambahkan `ControlPort 127.0.0.1:9051` +
`CookieAuthentication 0` di `/etc/tor/torrc`, restart `tor@default`.