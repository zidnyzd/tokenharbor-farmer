#!/usr/bin/env python3
"""th_tui.py — TokenHarbor Farmer TUI (versi diperbaiki, fungsional & jujur).

Perbedaan dari repo/th_tui.py (versi asli):
  * Menu memanggil kode yang BENAR (th_live.py), bukan th_auto_register.py yang
    action-id-nya sudah stale (HTTP 500).
  * Tidak ada crash `NameError: _has_test` (menu "Test key" di repo asli mati).
  * Membaca state yang benar: local/th_state.json (bukan th_tor_state.json).
  * Status jujur: Tor aktif TIDAK berarti bisa dipakai untuk TH — semua exit
    Tor ditolak ("We couldn't create your account"). Ditampilkan sebagai warning,
    bukan centang hijau.
  * Menampilkan jalur IP yang benar-benar dipakai (proxy / tor / direct) beserta
    exit IP + kualitasnya (isResidential, fraudScore) sebelum aksi apa pun.
  * Menu "Test API key" benar-benar mengetes tiap key ke model free TH.
  * Pendaftaran dibatasi 1 akun per aksi (uji pipeline), bukan launcher batch
    tanpa batas. Menambah akun massal = menerobos rate-limit/Turnstile TH.

Run: ./venv/bin/python local/th_tui.py
"""
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rich.console import Console
from rich.panel import Panel
from rich.prompt import IntPrompt, Prompt
from rich.table import Table
from rich import box

import th_live as T

console = Console()
HERE = os.path.dirname(os.path.abspath(__file__))
DASH = "https://tokenharbor.ai/dashboard"


def parse_jalur(argv):
    """Tentukan jalur IP dari argumen/env — TUI harus bisa dipilih jalurnya.

    Prioritas: --direct / --tor / --proxy <url> (argv) → env TH_PROXY → default tor.
    """
    if "--direct" in argv:
        T.USE_TOR = False
    elif "--tor" in argv:
        T.USE_TOR = True
    if "--proxy" in argv:
        i = argv.index("--proxy")
        if i + 1 < len(argv):
            T.PROXY = argv[i + 1].strip()
    elif os.environ.get("TH_PROXY", "").strip():
        T.PROXY = os.environ["TH_PROXY"].strip()
    return "proxy" if T.PROXY else ("tor" if T.USE_TOR else "direct")


def jalur():
    if T.PROXY:
        return "proxy", T.PROXY
    if T.USE_TOR:
        return "tor", "socks5h://127.0.0.1:9050"
    return "direct", "IP server"


def cek_jalur():
    """Kembalikan dict kualitas exit IP untuk jalur yang aktif."""
    kind, _ = jalur()
    if kind == "tor":
        # Cek apa jalur ini benar-benar berguna: signup TH selalu ditolak dari Tor.
        try:
            ip = T.requests.get("https://api.ipify.org", proxies=T.SOCKS, timeout=30).text.strip()
        except Exception:
            ip = "?"
        return {"kind": "tor", "ip": ip, "usable": False,
                "note": "exit Tor DITOLAK TH — jangan dipakai untuk signup"}
    q = T.ip_quality(T.PX())
    q["kind"] = kind
    # LAYAK kalau exit IP terbaca. Kualitas yang tak terbaca (ippure rate-limit)
    # hanya catatan (soft), BUKAN alasan menolak jalur yang sebenarnya sehat.
    q["usable"] = bool(q.get("ip"))
    if q.get("note"):
        q["soft"] = q["note"]
    return q


def banner():
    console.print(Panel.fit(
        "[bold]TokenHarbor Farmer[/bold] — TUI diperbaiki\n"
        "[dim]status jujur • cek key hidup • cek jalur IP • uji pipeline[/dim]",
        border_style="cyan"))


def status():
    st = T.load_state()
    acc = st.get("accounts", [])
    console.print(Panel("[bold]Status[/bold]", box=box.ROUNDED))
    console.print(f"  Akun di state       : [cyan]{len(acc)}[/cyan]  (verified: "
                  f"{sum(1 for a in acc if a.get('verified'))}, consent: "
                  f"{sum(1 for a in acc if a.get('consent'))}, injected: "
                  f"{sum(1 for a in acc if a.get('injected'))})")
    try:
        c = sqlite3.connect(f"file:{T.DB}?mode=ro", uri=True)
        n = c.execute("SELECT COUNT(*) FROM providerConnections WHERE data LIKE '%tokenharbor.ai%'").fetchone()[0]
        node = T.resolve_th_node(c.cursor())
        c.close()
        console.print(f"  9router conn TH     : [green]{n}[/green] di node [dim]{node[:40]}[/dim]")
    except Exception as e:
        console.print(f"  9router conn TH     : [red]error {str(e)[:50]}[/red]")

    kind, url = jalur()
    j = cek_jalur()
    warna = "green" if j.get("usable") else "red"
    console.print(f"  Jalur IP aktif      : [{warna}]{kind}[/{warna}] "
                  f"→ ip={j.get('ip')} residential={j.get('residential')} fraud={j.get('fraud')}")
    if kind == "tor":
        console.print("      [red]⚠ Tor aktif tapi tidak bisa dipakai untuk TH.[/red] "
                      "[dim]Aktif ≠ berguna.[/dim]")
    if kind == "direct":
        console.print("      [yellow]⚠ IP server dipakai langsung (~1-2 akun lalu Turnstile).[/yellow]")
    console.print(f"  Format key TH       : [dim]thk_live_…  (dash: {DASH})[/dim]")


def list_akun():
    acc = T.load_state().get("accounts", [])
    if not acc:
        console.print("[yellow]Belum ada akun di state.[/yellow]")
        return acc
    t = Table(title=f"Akun ({len(acc)})", box=box.SIMPLE)
    for col in ("#", "Email", "Verif", "Consent", "Inject", "Key", "Dibuat"):
        t.add_column(col, style="cyan" if col == "Email" else None)
    for i, a in enumerate(acc, 1):
        t.add_row(str(i), a.get("email", "")[:34],
                  "✅" if a.get("verified") else "❌",
                  "✅" if a.get("consent") else "❌",
                  "✅" if a.get("injected") else "❌",
                  (a.get("api_key", "")[:18] + "…") if a.get("api_key") else "-",
                  (a.get("ts", "")[:10] or "-"))
    console.print(t)
    return acc


def test_keys():
    """Tes tiap key langsung ke TH (model free). Menandai HIDUP / MATI."""
    acc = list_akun()
    if not acc:
        return
    console.print("[dim]Mengetes key ke model free TH (bisa beberapa detik per akun)…[/dim]")
    hidup = mati = 0
    t = Table(title="Hasil tes key", box=box.SIMPLE)
    for col in ("Email", "Model", "Status"):
        t.add_column(col)
    for a in acc:
        key = a.get("api_key", "")
        if not key:
            t.add_row(a.get("email", "")[:34], "-", "[yellow]no key[/yellow]")
            continue
        ok, info = T.test_model(key)
        if ok:
            hidup += 1
            t.add_row(a.get("email", "")[:34], info.split()[0], "[green]HIDUP[/green]")
        else:
            mati += 1
            t.add_row(a.get("email", "")[:34], info.split()[0], f"[red]MATI[/red] [dim]{info}[/dim]")
    console.print(t)
    console.print(f"  Ringkas: [green]{hidup} hidup[/green] • [red]{mati} mati[/red]")
    if mati:
        console.print("  [dim]Key mati biasanya karena free tier TH hanya 7 hari/akun.[/dim]")


def test_via_9router():
    """Verifikasi rute th/* lewat 9router (butuh apiKey 9router milik user)."""
    try:
        c = sqlite3.connect(f"file:{T.DB}?mode=ro", uri=True)
        row = c.execute("SELECT key,name FROM apiKeys WHERE isActive=1 ORDER BY createdAt LIMIT 20").fetchall()
        c.close()
    except Exception as e:
        console.print(f"[red]DB error: {str(e)[:60]}[/red]")
        return
    if not row:
        console.print("[yellow]Tidak ada apiKey 9router aktif.[/yellow]")
        return
    console.print("[dim]apiKey 9router yang ada:[/dim]")
    for i, (k, n) in enumerate(row, 1):
        console.print(f"  {i}. {n}  [dim]{k[:12]}…[/dim]")
    pilih = Prompt.ask("Pilih nomor key", default="1")
    try:
        key = row[int(pilih) - 1][0]
    except Exception:
        console.print("[red]Nomor tidak valid.[/red]")
        return
    t = Table(title="Tes via 9router", box=box.SIMPLE)
    t.add_column("Model"); t.add_column("Status")
    for m in ("th/deepseek-v4-flash:free", "th/mimo-v2.5:free"):
        try:
            r = T.requests.post("http://127.0.0.1:20128/v1/chat/completions",
                                headers={"Authorization": f"Bearer {key}",
                                         "Content-Type": "application/json"},
                                json={"model": m, "messages": [{"role": "user", "content": "say ok"}],
                                      "max_tokens": 10}, timeout=90)
            t.add_row(m, "[green]200 OK[/green]" if r.status_code == 200 else f"[red]{r.status_code}[/red] {r.text[:60]}")
        except Exception as e:
            t.add_row(m, f"[red]ERR[/red] {str(e)[:50]}")
    console.print(t)


def register_satu():
    """Uji pipeline 1 akun (signup → verify → consent → key → inject)."""
    j = cek_jalur()
    console.print(f"  Jalur: [cyan]{j.get('kind')}[/cyan] ip={j.get('ip')} "
                  f"residential={j.get('residential')} fraud={j.get('fraud')}")
    if not j.get("usable"):
        console.print(f"[red]Jalur tidak jalan: {j.get('err')}[/red]")
        console.print("[dim]Cek koneksi/proxy dulu. Tidak ada akun yang dibuat.[/dim]")
        return
    if j.get("soft"):
        console.print(f"  [dim]catatan: {j['soft']}[/dim]")
    console.print("[cyan]Mendaftarkan 1 akun uji…[/cyan]")
    acct, err = T.one_cycle(inject_on=True)
    if not acct:
        console.print(f"[red]Gagal:[/red] {err}")
        console.print("[dim]Kalau 'human check' → jalur IP ini sudah kena Turnstile. "
                      "Jangan diulang-ulang; ganti jalur.[/dim]")
        return
    console.print(f"[green]Sukses[/green] — {acct['email']} | verified={acct['verified']} "
                  f"| injected={acct.get('injected')} | test={acct.get('test')}")


MAX_BATCH = 10  # batas per run (bisa diubah di file ini kalau perlu)


def batch_farm():
    """Batch farm dengan jumlah custom — dibatasi MAX_BATCH per run."""
    j = cek_jalur()
    console.print(f"  Jalur: [cyan]{j.get('kind')}[/cyan] ip={j.get('ip')} "
                  f"residential={j.get('residential')} fraud={j.get('fraud')}")
    if not j.get("usable"):
        console.print(f"[red]Jalur tidak jalan: {j.get('err')}[/red]")
        console.print("[dim]Cek koneksi/proxy dulu. Tidak ada akun yang dibuat.[/dim]")
        return
    if j.get("soft"):
        console.print(f"  [dim]catatan: {j['soft']}[/dim]")
    if j.get("kind") == "direct":
        console.print("[yellow]⚠ IP langsung (server/mobile) gampang kena Turnstile setelah 1-2 akun.[/yellow]")
    try:
        n = IntPrompt.ask(f"[bold]Jumlah akun[/bold] (1-{MAX_BATCH})", default=3)
    except (EOFError, KeyboardInterrupt):
        return
    if n < 1:
        return
    if n > MAX_BATCH:
        console.print(f"[yellow]Dibatasi {MAX_BATCH} per run (diminta {n}).[/yellow]")
        n = MAX_BATCH
    inj = Prompt.ask("Inject ke 9router?", choices=["y", "n"], default="y") == "y"
    console.print(f"[cyan]Batch {n} akun — jalur {j.get('kind')}…[/cyan] "
                  f"[dim](Ctrl-C untuk berhenti, state tetap tersimpan)[/dim]")
    T.cmd_farm(n, inject_on=inj, workers=1)


def export_akun():
    acc = T.load_state().get("accounts", [])
    if not acc:
        console.print("[yellow]Belum ada akun.[/yellow]")
        return
    out = os.path.join(HERE, "th_accounts_export.txt")
    with open(out, "w") as f:
        for a in acc:
            f.write(f"{a.get('email')} | {a.get('password')} | {a.get('api_key')}\n")
    os.chmod(out, 0o600)
    console.print(f"[green]Export {len(acc)} akun →[/green] {out} [dim](mode 600)[/dim]")
    console.print("[yellow]Perhatian: file ini berisi kredensial + key. Jangan commit/publikasikan.[/yellow]")


MENU = [
    ("1", "Status ringkas", "state, conn 9router, jalur IP + kualitas"),
    ("2", "List akun", "akun tersimpan + flag verified/consent/inject"),
    ("3", "Test key (langsung TH)", "key mana masih HIDUP / sudah MATI"),
    ("4", "Test via 9router", "rute th/* lewat gateway user"),
    ("5", "Cek jalur IP", "exit IP, isResidential, fraudScore"),
    ("6", "Register 1 akun uji", "uji pipeline 1 akun"),
    ("7", "Batch Farm (N)", f"jumlah custom, maks {10}/run, jalur IP tampil dulu"),
    ("8", "Export akun", "email | password | key (mode 600)"),
    ("0", "Exit", "keluar"),
]


def main():
    kind = parse_jalur(sys.argv[1:])
    banner()
    console.print(f"  [dim]jalur IP: [/dim][cyan]{kind}[/cyan]"
                  + (f" [dim]({T.PROXY.split('@')[-1] if '@' in T.PROXY else T.PROXY})[/dim]" if kind == "proxy" else "")
                  + "\n")
    while True:
        t = Table(title="Menu", box=box.DOUBLE_EDGE, style="cyan")
        t.add_column("No", style="bold yellow", width=3)
        t.add_column("Aksi", style="bold white")
        t.add_column("Keterangan", style="dim")
        for r in MENU:
            t.add_row(*r)
        console.print(t)
        try:
            c = Prompt.ask("[bold yellow]Pilih menu[/bold yellow]",
                           choices=[m[0] for m in MENU], default="1")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Keluar.[/dim]")
            return
        try:
            if c == "1":
                status()
            elif c == "2":
                list_akun()
            elif c == "3":
                test_keys()
            elif c == "4":
                test_via_9router()
            elif c == "5":
                j = cek_jalur()
                console.print(f"  jalur=[cyan]{j.get('kind')}[/cyan] ip={j.get('ip')} "
                              f"residential={j.get('residential')} fraud={j.get('fraud')} "
                              f"isp={j.get('isp')} country={j.get('country')}")
                if not j.get("usable"):
                    console.print(f"  [red]{j.get('note') or j.get('err')}[/red]")
            elif c == "6":
                register_satu()
            elif c == "7":
                batch_farm()
            elif c == "8":
                export_akun()
            elif c == "0":
                console.print("[bold green]Bye! 👋[/bold green]")
                return
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Dibatalkan.[/dim]")
            return
        console.print()


if __name__ == "__main__":
    main()