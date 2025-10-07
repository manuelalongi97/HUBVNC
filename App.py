# HubVNC.py — UI stile RealVNC con cartelle riordinabili, DnD connessioni, vista Lista/Icone, LED ping,
# avvio TightVNC con form password/porta (HKCU), reset password VNC remoto (HKLM) ASINCRONO, statusbar e logging.
# Extra: default password VNC "500rossa" nelle nuove connessioni VNC, ping silenzioso (niente finestre nere),
# autostart TightVNC all'avvio dell'app, e AUTO-AVVIO dell'APP a login Windows (menu Impostazioni → Configurazione…).
# Nota: la password delle connessioni VNC è salvata in chiaro nel connections.json per semplicità.
# + Aggiornamento: Verifica aggiornamenti da GitHub con auto-update (solo versione script).

VERSION = "1.2.0"   # aggiorna ad ogni release

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import subprocess, os, sys, json, shutil, platform, getpass, socket
import threading, time, webbrowser, tempfile
from datetime import datetime

# Networking per "Check for updates"
import ssl
import urllib.request, urllib.error

# opzionali (Windows)
try:
    from Crypto.Cipher import DES  # pycryptodome
except Exception:
    DES = None

try:
    import winreg
except Exception:
    winreg = None

# ───────── Config / percorsi ─────────
RESET_ON_START = ("--reset" in sys.argv)
ALLOW_IMPORT_FROM_EXE = False
AUTOSTART_VNC_ON_LAUNCH = True  # avvia TightVNC server all'apertura dell'app

# Config GitHub per aggiornamenti
GITHUB_REPO = "Lyoneega/HubVNC"   # <owner>/<repo>
RAW_BRANCH = "main"                # branch dove sta il file principale
RAW_FILENAME = os.path.basename(__file__) if not getattr(sys, "frozen", False) else "App.py"
VERSION_FILE_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{RAW_BRANCH}/VERSION.txt"
RAW_SCRIPT_URL  = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{RAW_BRANCH}/{RAW_FILENAME}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
REPO_RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
    RESOURCE_DIR = sys._MEIPASS
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    RESOURCE_DIR = APP_DIR

APPDATA_DIR = os.path.join(os.environ.get("APPDATA", APP_DIR), "HubVNC")
os.makedirs(APPDATA_DIR, exist_ok=True)
SAVE_FILE = os.path.join(APPDATA_DIR, "connections.json")
SETTINGS_FILE = os.path.join(APPDATA_DIR, "settings.json")
LOG_FILE  = os.path.join(APPDATA_DIR, "hubvnc.log")

VNC_VIEWER  = os.path.join(RESOURCE_DIR, "tvnviewer.exe")
VNC_SERVER  = os.path.join(RESOURCE_DIR, "tvnserver.exe")
CONN_ICON_PATH = os.path.join(RESOURCE_DIR, "conn_ico.png")
OLD_LOCAL_SAVE = os.path.join(APP_DIR, "connections.json")

# ───────── logging ─────────
def log(msg: str):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {msg}\n")
    except Exception:
        pass

# ───────── settings ─────────
DEFAULT_SETTINGS = {
    "autostart_app": False,
    # Modalità admin
    "admin_enabled": False,
    "admin_password": ""
}

def load_settings():
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                s = json.load(f)
            for k, v in DEFAULT_SETTINGS.items():
                s.setdefault(k, v)
            return s
    except Exception:
        pass
    return dict(DEFAULT_SETTINGS)

def save_settings(s: dict):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

settings = load_settings()
is_admin = False  # stato di sessione per visibilità funzioni admin

# ───────── auto-start APP su Windows (HKCU\...\Run) ─────────
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "HubVNC"

def _exe_for_autostart():
    # preferisci .exe quando frozen; altrimenti pythonw + script
    if getattr(sys, "frozen", False):
        return f"\"{sys.executable}\""
    else:
        pyw = shutil.which("pythonw.exe") or shutil.which("python.exe") or "python"
        return f"\"{pyw}\" \"{os.path.abspath(__file__)}\""

def is_autostart_enabled():
    if not winreg: return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, RUN_VALUE_NAME)
            return bool(val)
    except Exception:
        return False

def enable_autostart():
    if not winreg:
        messagebox.showerror("Errore", "API registro non disponibili su questo sistema.")
        return False
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.SetValueEx(k, RUN_VALUE_NAME, 0, winreg.REG_SZ, _exe_for_autostart())
        log("Enabled app autostart (Windows Run key)")
        return True
    except Exception as e:
        messagebox.showerror("Errore", f"Impossibile attivare l'avvio automatico:\n{e}")
        return False

def disable_autostart():
    if not winreg:
        messagebox.showerror("Errore", "API registro non disponibili su questo sistema.")
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            try:
                winreg.DeleteValue(k, RUN_VALUE_NAME)
            except FileNotFoundError:
                pass
        log("Disabled app autostart (Windows Run key)")
        return True
    except Exception as e:
        messagebox.showerror("Errore", f"Impossibile disattivare l'avvio automatico:\n{e}")
        return False

# ───────── utility run silenzioso (no finestre nere su Windows) ─────────
def _silent_flags_kwargs():
    flags = {}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags["creationflags"] = subprocess.CREATE_NO_WINDOW
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        flags["startupinfo"] = si
    return flags

def run_silent(args, **kwargs):
    flags = _silent_flags_kwargs()
    flags.update(kwargs)
    return subprocess.run(args, **flags)

def popen_silent(args, **kwargs):
    flags = _silent_flags_kwargs()
    flags.update(kwargs)
    return subprocess.Popen(args, **flags)

# ───────── Sezioni / ordine persistente ─────────
sections = {}
section_order = []  # es. ["Tutte","Produzione","Test",...]
current_section = "Tutte"

def _write_empty_connections(path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"Tutte": [], "Produzione": [], "Test": [], "__order__": ["Tutte","Produzione","Test"]},
                  f, indent=2, ensure_ascii=False)

def ensure_connections_file():
    if RESET_ON_START and os.path.exists(SAVE_FILE):
        try: os.remove(SAVE_FILE)
        except Exception: pass
    if not os.path.exists(SAVE_FILE):
        _write_empty_connections(SAVE_FILE)

def load_connections():
    global section_order
    try:
        with open(SAVE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "__order__" in data:
            section_order = list(data.pop("__order__"))
        else:
            section_order = sorted(data.keys(), key=str.lower)
            if "Tutte" in section_order:
                section_order.remove("Tutte"); section_order = ["Tutte"] + section_order
        return data
    except Exception:
        section_order = ["Tutte","Produzione","Test"]
        return {"Tutte":[],"Produzione":[],"Test":[]}

def save_connections():
    try:
        payload = dict(sections)
        payload["__order__"] = section_order
        with open(SAVE_FILE,"w",encoding="utf-8") as f:
            json.dump(payload,f,indent=2,ensure_ascii=False)
    except Exception as e:
        messagebox.showerror("Errore", f"Errore nel salvataggio:\n{e}")

ensure_connections_file()
sections = load_connections()

# ───────── Info PC (statusbar) ─────────
import platform
def get_primary_ipv4():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]; s.close(); return ip
    except Exception:
        try:
            hostname = socket.gethostname()
            for fam, _, _, _, sockaddr in socket.getaddrinfo(hostname, None):
                if fam == socket.AF_INET:
                    cand = sockaddr[0]
                    if not cand.startswith("127."): return cand
        except Exception: pass
        return "N/D"

def get_pc_info():
    try: hostname = socket.gethostname()
    except Exception: hostname = "N/D"
    try: user = getpass.getuser()
    except Exception: user = "N/D"
    try: osver = f"{platform.system()} {platform.release()} ({platform.version()})"
    except Exception: osver = platform.platform() if hasattr(platform,"platform") else "N/D"
    ip = get_primary_ipv4()
    return hostname, user, osver, ip

# ───────── Colori / stato selezione ─────────
BG_MAIN, BG_SIDEBAR, BG_TILE = "#f5f7fb", "#eef2f7", "#f9fafb"
TILE_BG, TILE_HOVER, TILE_SELECTED = BG_TILE, "#eaf2ff", "#d6e4ff"
selected_tile = None
selected_conn = None
_mouse_down_tile = None

# ───────── Ping cache (LED) ─────────
PING_TTL = 30
_ping_cache = {}  # host -> (status_bool|None, ts)

def _ping_host_once(host: str, timeout_ms: int = 600) -> bool | None:
    if not host: return None
    try:
        r = run_silent(["ping","-n","1","-w", str(timeout_ms), host],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return (r.returncode == 0)
    except Exception:
        return None

def _ping_async(host: str, on_done):
    def _worker():
        st = _ping_host_once(host); _ping_cache[host] = (st, time.time())
        root.after(0, lambda: on_done(host, st))
    threading.Thread(target=_worker, daemon=True).start()

def _need_ping(host: str) -> bool:
    st = _ping_cache.get(host)
    return (not st) or (time.time() - st[1]) > PING_TTL

def _set_led_color(widget: tk.Label, status: bool | None):
    color = "#22c55e" if status is True else ("#ef4444" if status is False else "#9ca3af")
    try: widget.configure(fg=color)
    except Exception: pass

# ───────── DnD connessioni ─────────
def find_connection_by_values(values):
    name, host, port, proto = values
    for sec, lst in sections.items():
        for c in lst:
            if c.get("name")==name and c.get("host")==host and str(c.get("port"))==str(port) and c.get("protocol")==proto:
                return c, sec
    return None, None

def move_connection_to_section(conn_dict, from_sec, to_sec):
    if not conn_dict or not to_sec or to_sec not in sections or to_sec=="Tutte": return False
    if from_sec and from_sec in sections and conn_dict in sections[from_sec]: sections[from_sec].remove(conn_dict)
    sections[to_sec].append(conn_dict); save_connections(); log(f"Moved connection '{conn_dict.get('name')}' to folder '{to_sec}'"); return True

drag_data = {"values": None, "from_section": None, "source": None}
drag_preview = None; section_hover_row = None; original_cursor = None
_drag_start_xy = None; _drag_active = False; _DRAG_THRESHOLD = 12

def create_drag_preview(values):
    global drag_preview
    if drag_preview is not None: return
    name, host, port, proto = values
    drag_preview = tk.Toplevel(root); drag_preview.overrideredirect(True)
    try: drag_preview.attributes("-topmost", True); drag_preview.attributes("-alpha", 0.9)
    except Exception: pass
    frame = tk.Frame(drag_preview, bg="#1a73e8", bd=1, relief="solid"); frame.pack(fill="both", expand=True)
    inner = tk.Frame(frame, bg="#ffffff"); inner.pack(padx=1, pady=1)
    tk.Label(inner, text=f"{name}  ({host}:{port} {proto})", bg="#ffffff", font=("Segoe UI", 9)).pack(padx=6, pady=3)

def destroy_drag_preview():
    global drag_preview
    if drag_preview is not None:
        try: drag_preview.destroy()
        except Exception: pass
        drag_preview = None

def update_drag_preview_position():
    if drag_preview is not None:
        x, y = root.winfo_pointerxy(); drag_preview.geometry(f"+{x+15}+{y+15}")

def highlight_section_under_pointer():
    global section_hover_row
    x, y = root.winfo_pointerxy(); target = root.winfo_containing(x, y)
    if not target: clear_section_hover(); return
    if target is section_list or str(target).startswith(str(section_list)):
        local_y = y - section_list.winfo_rooty(); row = section_list.identify_row(local_y)
        if row:
            if section_hover_row != row:
                clear_section_hover(); section_list.selection_set(row); section_hover_row = row
            return
    clear_section_hover()

def clear_section_hover():
    global section_hover_row
    if section_hover_row:
        try: section_list.selection_remove(section_hover_row)
        except Exception: pass
        section_hover_row = None

def set_drag_cursor(active: bool):
    global original_cursor
    try:
        if active:
            if original_cursor is None: original_cursor = root["cursor"]
            root.configure(cursor="hand2")
        else:
            if original_cursor is not None: root.configure(cursor=original_cursor); original_cursor = None
    except Exception: pass

def _maybe_start_drag():
    global _drag_active
    if not drag_data["values"] or _drag_active or _drag_start_xy is None: return
    x0, y0 = _drag_start_xy; x1, y1 = root.winfo_pointerxy()
    if abs(x1-x0) >= _DRAG_THRESHOLD or abs(y1-y0) >= _DRAG_THRESHOLD:
        _drag_active = True; create_drag_preview(drag_data["values"]); update_drag_preview_position(); set_drag_cursor(True)

def perform_drop(event=None):
    if not drag_data.get("values"):
        return
    x, y = root.winfo_pointerxy()
    target_widget = root.winfo_containing(x, y)
    to_section = None
    if target_widget is section_list or str(target_widget).startswith(str(section_list)):
        ry = y - section_list.winfo_rooty(); row_id = section_list.identify_row(ry)
        if row_id: to_section = section_list.item(row_id)["text"]
    if not to_section:
        sel = section_list.focus()
        if sel: to_section = section_list.item(sel)["text"]
    if to_section and to_section in sections and to_section != "Tutte":
        conn, from_sec = find_connection_by_values(drag_data["values"])
        if conn and move_connection_to_section(conn, from_sec, to_section):
            refresh_connections()
    drag_data["values"]=None; drag_data["from_section"]=None; drag_data["source"]=None
    destroy_drag_preview(); clear_section_hover(); set_drag_cursor(False)

def on_global_mouse_move(event=None):
    if drag_data["values"] and _drag_active: update_drag_preview_position(); highlight_section_under_pointer()

# ───────── DnD cartelle (riordino sidebar) ─────────
_sec_drag_name = None
_sec_drag_from_idx = None

def _section_name_at_y(y_root):
    local_y = y_root - section_list.winfo_rooty()
    row = section_list.identify_row(local_y)
    if not row: return None, None
    name = section_list.item(row)["text"]
    return name, row

def _on_section_btn1(e):
    global _sec_drag_name, _sec_drag_from_idx
    name, _ = _section_name_at_y(e.y_root)
    _sec_drag_name = None; _sec_drag_from_idx = None
    if not name: return
    _sec_drag_name = name
    try: _sec_drag_from_idx = section_order.index(name)
    except ValueError: _sec_drag_from_idx = None

def _on_section_btn1_release(e):
    global _sec_drag_name, _sec_drag_from_idx
    if not _sec_drag_name or _sec_drag_from_idx is None: return
    target_name, _ = _section_name_at_y(e.y_root)
    if not target_name or target_name == _sec_drag_name:
        _sec_drag_name=None; _sec_drag_from_idx=None; return
    if _sec_drag_name == "Tutte":
        _sec_drag_name=None; _sec_drag_from_idx=None; return
    try:
        from_idx = _sec_drag_from_idx
        to_idx   = section_order.index(target_name)
        if section_order and section_order[0] == "Tutte" and to_idx == 0:
            to_idx = 1
        section_order.pop(from_idx); section_order.insert(to_idx, _sec_drag_name)
        refresh_sections_tree(); save_connections(); log(f"Reordered folder '{_sec_drag_name}' -> index {to_idx}")
    except Exception:
        pass
    finally:
        _sec_drag_name=None; _sec_drag_from_idx=None

def move_section_up():
    sel = section_list.focus()
    if not sel: return
    name = section_list.item(sel)["text"]
    if name == "Tutte": return
    try:
        i = section_order.index(name)
        min_i = 1 if section_order and section_order[0] == "Tutte" else 0
        if i > min_i:
            section_order[i-1], section_order[i] = section_order[i], section_order[i-1]
            refresh_sections_tree(); save_connections(); log(f"Folder '{name}' moved up")
    except ValueError: pass

def move_section_down():
    sel = section_list.focus()
    if not sel: return
    name = section_list.item(sel)["text"]
    if name == "Tutte": return
    try:
        i = section_order.index(name)
        if i < len(section_order)-1:
            section_order[i+1], section_order[i] = section_order[i], section_order[i+1]
            refresh_sections_tree(); save_connections(); log(f"Folder '{name}' moved down")
    except ValueError: pass

def sort_sections_az():
    global section_order
    others = [s for s in section_order if s != "Tutte"]
    section_order = (["Tutte"] if "Tutte" in section_order else []) + sorted(others, key=str.lower)
    refresh_sections_tree(); save_connections(); log("Folders sorted A→Z")

# ───────── UI base ─────────
root = tk.Tk()
root.title("Hub VNC/RDP/SSH - All-in-One")
root.geometry("1100x640"); root.minsize(900, 560)
root.grid_rowconfigure(0, weight=1); root.grid_columnconfigure(0, weight=1)

style = ttk.Style()
try: style.theme_use("vista")
except Exception: pass
DEFAULT_FONT, BOLD_FONT = ("Segoe UI", 9), ("Segoe UI", 10, "bold")
root.option_add("*Font", DEFAULT_FONT)
root.configure(bg=BG_MAIN)

# Menu (IMPOSTAZIONI)
menubar = tk.Menu(root)
settings_menu = tk.Menu(menubar, tearoff=0)

def export_connections():
    path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON","*.json")])
    if not path: return
    try:
        payload = dict(sections); payload["__order__"] = section_order
        with open(path,"w",encoding="utf-8") as f: json.dump(payload,f,indent=2,ensure_ascii=False)
        messagebox.showinfo("Esportazione","Connessioni esportate."); log(f"Exported connections to {path}")
    except Exception as e: messagebox.showerror("Errore",f"Impossibile esportare:\n{e}")

def import_connections():
    global sections, section_order, current_section
    path = filedialog.askopenfilename(filetypes=[("JSON","*.json")])
    if not path: return
    try:
        with open(path,"r",encoding="utf-8") as f: data=json.load(f)
        section_order = list(data.pop("__order__", [])) or ["Tutte"] + sorted([k for k in data.keys() if k!="Tutte"], key=str.lower)
        sections = data; current_section="Tutte"
        refresh_sections_tree(); refresh_connections(); save_connections()
        messagebox.showinfo("Importazione","Connessioni importate."); log(f"Imported connections from {path}")
    except Exception as e: messagebox.showerror("Errore",f"Impossibile importare:\n{e}")

def reset_connections():
    global sections, section_order, current_section
    _write_empty_connections(SAVE_FILE)
    sections = load_connections(); current_section="Tutte"
    refresh_sections_tree(); refresh_connections()
    messagebox.showinfo("Reimpostato","Connessioni azzerate."); log("Connections reset to defaults")

# ───────── Check for updates (GitHub) ─────────
def _parse_version(v: str):
    v = (v or "").strip().lstrip("vV")
    parts = []
    for p in v.split("."):
        try:
            parts.append(int("".join(ch for ch in p if ch.isdigit())))
        except Exception:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])

def _http_get(url: str, timeout=10, accept="*/*"):
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={
        "User-Agent": "HubVNC-Updater",
        "Accept": accept
    })
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read()

def _get_latest_version_from_version_file():
    try:
        data = _http_get(VERSION_FILE_URL, accept="text/plain")
        return (data.decode("utf-8", "ignore").strip() or "").splitlines()[0].strip()
    except Exception:
        return None

def _get_latest_release_via_api():
    try:
        data = _http_get(LATEST_RELEASE_API, accept="application/json")
        j = json.loads(data.decode("utf-8", "ignore"))
        tag = j.get("tag_name") or j.get("name") or ""
        assets = j.get("assets") or []
        # prova a cercare un eseguibile .exe tra gli asset
        exe_url = None
        for a in assets:
            url = a.get("browser_download_url") or ""
            name = (a.get("name") or "").lower()
            if name.endswith(".exe") or name.endswith(".msi"):
                exe_url = url
                break
        return tag, exe_url
    except Exception:
        return None, None

def _self_update_script(new_text: str):
    """Sostituisce il file sorgente corrente con il testo nuovo, creando un .bak, poi propone riavvio."""
    try:
        current = os.path.abspath(__file__)
        bak = current + ".bak"
        with open(bak, "w", encoding="utf-8") as f:
            with open(current, "r", encoding="utf-8", errors="ignore") as oldf:
                f.write(oldf.read())
        with open(current, "w", encoding="utf-8") as f:
            f.write(new_text)
        return True, bak
    except Exception as e:
        return False, str(e)

def check_for_updates():
    # Finestra di progresso
    prog = tk.Toplevel(root)
    prog.title("Verifica aggiornamenti…")
    prog.resizable(False, False)
    frm = ttk.Frame(prog, padding=12); frm.pack(fill="both", expand=True)
    ttk.Label(frm, text=f"Versione attuale: {VERSION}\nControllo su GitHub…").pack(pady=(2,8))
    bar = ttk.Progressbar(frm, mode="indeterminate", length=280); bar.pack(); bar.start(10)
    btn_box = ttk.Frame(frm); btn_box.pack(fill="x", pady=(8,0))
    ttk.Button(btn_box, text="Chiudi", command=prog.destroy).pack(side="right")
    prog.transient(root)
    try: prog.attributes("-topmost", True)
    except Exception: pass
    prog.grab_set()

    def finish_ok(msg):
        try:
            bar.stop(); prog.destroy()
        except Exception:
            pass
        messagebox.showinfo("Aggiornamenti", msg)

    def finish_err(msg):
        try:
            bar.stop(); prog.destroy()
        except Exception:
            pass
        messagebox.showerror("Aggiornamenti", msg)

    def worker():
        try:
            # 1) Prova prima con VERSION.txt (semplice)
            latest = _get_latest_version_from_version_file()
            exe_url = None
            if not latest:
                # 2) fallback API releases
                latest, exe_url = _get_latest_release_via_api()
            if not latest:
                root.after(0, lambda: finish_err("Impossibile recuperare la versione più recente."))
                return

            cur = _parse_version(VERSION)
            new = _parse_version(latest)
            if new <= cur:
                root.after(0, lambda: finish_ok(f"Hai già l'ultima versione ({VERSION})."))
                return

            # C'è un aggiornamento!
            if getattr(sys, "frozen", False):
                # App impacchettata: apri la pagina delle release o asset .exe se trovato
                def _ask_open():
                    if exe_url:
                        if messagebox.askyesno("Nuova versione disponibile",
                            f"Trovata versione {latest}.\nVuoi aprire il download dell'installer?"):
                            webbrowser.open(exe_url)
                    else:
                        if messagebox.askyesno("Nuova versione disponibile",
                            f"Trovata versione {latest}.\nVuoi aprire la pagina delle release?"):
                            webbrowser.open(REPO_RELEASES_PAGE)
                root.after(0, _ask_open)
            else:
                # Script Python: scarica il nuovo HubVNC.py dal branch
                def _do_script_update():
                    try:
                        data = _http_get(RAW_SCRIPT_URL, accept="text/plain")
                        text = data.decode("utf-8", "ignore")
                        ok, info = _self_update_script(text)
                        if ok:
                            messagebox.showinfo("Aggiornamento completato",
                                f"Aggiornato a {latest}.\nBackup creato: {info}\nRiavvia l'app per applicare le modifiche.")
                        else:
                            messagebox.showerror("Errore aggiornamento", f"Impossibile aggiornare il file:\n{info}")
                    except Exception as e:
                        messagebox.showerror("Errore aggiornamento", f"Download fallito:\n{e}")
                def _ask_update():
                    if messagebox.askyesno("Nuova versione disponibile",
                            f"Trovata versione {latest}.\nVuoi aggiornare automaticamente questo script?"):
                        _do_script_update()
                root.after(0, _ask_update)
        except Exception as e:
            root.after(0, lambda: finish_err(f"Errore durante il controllo aggiornamenti:\n{e}"))

    threading.Thread(target=worker, daemon=True).start()

def open_settings_dialog():
    dlg = tk.Toplevel(root); dlg.title("Configurazione"); dlg.resizable(False, False)
    frm = ttk.Frame(dlg, padding=12); frm.pack(fill="both", expand=True)

    # Autostart app
    autostart_var = tk.BooleanVar(value=is_autostart_enabled())
    ttk.Checkbutton(frm, text="Avvia HubVNC all'accesso a Windows", variable=autostart_var).grid(row=0, column=0, sticky="w", pady=4)

    # Admin abilitazione
    admin_enabled_var = tk.BooleanVar(value=settings.get("admin_enabled", False))
    ttk.Checkbutton(frm, text="Abilita login Admin (mostra funzioni avanzate)", variable=admin_enabled_var)\
        .grid(row=1, column=0, sticky="w", pady=4)

    def set_admin_password():
        if not admin_enabled_var.get():
            messagebox.showwarning("Attenzione", "Abilita prima il login Admin.")
            return
        pwd = simpledialog.askstring("Password Admin", "Imposta/Modifica password Admin:", show="*")
        if pwd is None:  # annulla
            return
        settings["admin_password"] = pwd or ""
        save_settings(settings)
        messagebox.showinfo("OK", "Password Admin aggiornata.")

    ttk.Button(frm, text="Imposta Password Admin…", command=set_admin_password).grid(row=2, column=0, sticky="w", pady=(0,8))

    # Verifica aggiornamenti
    ttk.Button(frm, text="Verifica aggiornamenti…", command=check_for_updates).grid(row=3, column=0, sticky="w", pady=(0,8))

    btns = ttk.Frame(frm); btns.grid(row=10, column=0, sticky="e", pady=(10,0))
    def do_ok():
        want = autostart_var.get()
        ok = True
        if want and not is_autostart_enabled():
            ok = enable_autostart()
        elif (not want) and is_autostart_enabled():
            ok = disable_autostart()
        if ok:
            settings["autostart_app"] = want
            settings["admin_enabled"] = admin_enabled_var.get()
            save_settings(settings)
            dlg.destroy()
    ttk.Button(btns, text="OK", command=do_ok).pack(side="right", padx=(6,0))
    ttk.Button(btns, text="Annulla", command=dlg.destroy).pack(side="right")

# Voci menu impostazioni classiche
settings_menu.add_command(label="Configurazione…", command=open_settings_dialog)
settings_menu.add_command(label="Verifica aggiornamenti…", command=check_for_updates)
settings_menu.add_separator()
settings_menu.add_command(label="Importa…", command=import_connections)
settings_menu.add_command(label="Esporta…", command=export_connections)
settings_menu.add_separator()
settings_menu.add_command(label="Reimposta connessioni", command=reset_connections)
settings_menu.add_separator()

# Login/Logout Admin (versione robusta con auto-enable)
def admin_login():
    global is_admin
    # Se non abilitato ma una password esiste, abilita automaticamente
    if not settings.get("admin_enabled") and settings.get("admin_password", ""):
        settings["admin_enabled"] = True
        save_settings(settings)

    if not settings.get("admin_enabled"):
        # Offri di abilitarlo al volo
        if messagebox.askyesno("Admin", "Il login Admin non è abilitato. Vuoi abilitarlo ora?"):
            pwd_set = simpledialog.askstring("Password Admin", "Imposta password Admin:", show="*")
            if pwd_set:
                settings["admin_enabled"] = True
                settings["admin_password"] = pwd_set
                save_settings(settings)
            else:
                messagebox.showwarning("Admin", "Operazione annullata.")
                return
        else:
            return

    pwd = simpledialog.askstring("Login Admin", "Password Admin:", show="*")
    if pwd is None:
        return
    if pwd == settings.get("admin_password", "") and pwd != "":
        is_admin = True
        update_admin_visibility()   # FORZA visibilità
        messagebox.showinfo("Admin", "Accesso Admin eseguito.")
        log("Admin login")
    else:
        messagebox.showerror("Admin", "Password errata.")

def admin_logout():
    global is_admin
    was = is_admin
    is_admin = False
    update_admin_visibility()       # FORZA visibilità
    if was:
        messagebox.showinfo("Admin", "Uscito dalla modalità Admin.")
        log("Admin logout")

settings_menu.add_command(label="Accedi come Admin…", command=admin_login)
settings_menu.add_command(label="Esci Admin", command=admin_logout)

settings_menu.add_separator()
settings_menu.add_command(label="Esci", command=root.destroy)

menubar.add_cascade(label="Impostazioni", menu=settings_menu)
root.config(menu=menubar)

# Layout principale
paned = ttk.Panedwindow(root, orient="horizontal"); paned.grid(row=0,column=0,sticky="nsew", padx=8, pady=8)
left = tk.Frame(paned, bg=BG_SIDEBAR); right = tk.Frame(paned, bg=BG_MAIN)
paned.add(left, weight=1); paned.add(right, weight=4)

# Sidebar sinistra
sb_head = tk.Frame(left, bg=BG_SIDEBAR); sb_head.pack(fill="x", padx=10, pady=(10,6))
tk.Label(sb_head, text="Hub VNC/RDP/SSH", font=BOLD_FONT, bg=BG_SIDEBAR).pack(anchor="w")
sb_actions = tk.Frame(left, bg=BG_SIDEBAR); sb_actions.pack(fill="x", padx=10, pady=(4,10))

def _sbbtn(parent, text, cmd):
    b=ttk.Button(parent,text=text,command=cmd); b.pack(fill="x",pady=3); return b

_sbbtn(sb_actions,"📁 Nuova cartella",    lambda: add_section_popup())
_sbbtn(sb_actions,"✎ Rinomina cartella",  lambda: rename_section_popup())
_sbbtn(sb_actions,"🗑 Elimina cartella",   lambda: remove_section())
_sbbtn(sb_actions,"⬆ Sposta su",          move_section_up)
_sbbtn(sb_actions,"⬇ Sposta giù",         move_section_down)
_sbbtn(sb_actions,"↔ Ordina A→Z",         sort_sections_az)

ttk.Separator(left, orient="horizontal").pack(fill="x", padx=10, pady=(0,8))

frame_sections = tk.Frame(left, bg=BG_SIDEBAR); frame_sections.pack(expand=True, fill="both", padx=10, pady=(0,10))
sec_scroll = ttk.Scrollbar(frame_sections, orient="vertical")
section_list = ttk.Treeview(frame_sections, show="tree", yscrollcommand=sec_scroll.set, selectmode="browse")
sec_scroll.config(command=section_list.yview); sec_scroll.pack(side="right", fill="y"); section_list.pack(side="left", expand=True, fill="both")

def refresh_sections_tree():
    section_list.delete(*section_list.get_children())
    for sec in section_order:
        if sec in sections: section_list.insert("", "end", text=sec)
refresh_sections_tree()

section_list.bind("<Button-1>", _on_section_btn1, add="+")
section_list.bind("<ButtonRelease-1>", _on_section_btn1_release, add="+")

# Pannello destro: toolbar + content
content = tk.Frame(right, bg=BG_MAIN); content.pack(expand=True, fill="both", padx=10, pady=10)
toolbar = tk.Frame(content, bg=BG_MAIN); toolbar.pack(fill="x", pady=(0,8))
filter_var = tk.StringVar(); view_mode = tk.StringVar(value="icone")

ttk.Button(toolbar, text="➕ Nuova",   command=lambda: add_connection_popup(False)).pack(side="left", padx=(0,6))
ttk.Button(toolbar, text="✏️ Modifica", command=lambda: add_connection_popup(True)).pack(side="left", padx=(0,6))
ttk.Button(toolbar, text="🗑 Rimuovi",  command=lambda: remove_connection()).pack(side="left", padx=(0,6))
ttk.Button(toolbar, text="Connetti",   command=lambda: connect_to_selected()).pack(side="left", padx=(0,6))
ttk.Button(toolbar, text="▶ Avvia Server VNC", command=lambda: start_vnc_server()).pack(side="left", padx=(0,12))

# Bottone Reset VNC — creato qui ma mostrato solo da update_admin_visibility()
btn_reset_vnc = ttk.Button(toolbar, text="🔑 Reset Psw VNC Remoto", command=lambda: reset_vnc_password_remote())

tk.Frame(toolbar, bg=BG_MAIN).pack(side="left", expand=True, fill="x")
ttk.Label(toolbar, text="Cerca:").pack(side="left", padx=(0,4))
ttk.Entry(toolbar, textvariable=filter_var, width=28).pack(side="left", padx=(0,12))
ttk.Button(toolbar, text="Vista: Lista",  command=lambda: set_view_mode("lista")).pack(side="left", padx=(0,6))
ttk.Button(toolbar, text="Vista: Icone",  command=lambda: set_view_mode("icone")).pack(side="left")

def update_admin_visibility():
    """Mostra/nasconde le funzioni Admin in base a is_admin (forzato)."""
    try:
        if is_admin:
            btn_reset_vnc.pack(side="left", padx=(0,12))
        else:
            btn_reset_vnc.pack_forget()
    except Exception:
        pass

center = tk.Frame(content, bg=BG_MAIN); center.pack(expand=True, fill="both")

# Lista
cols = ("Stato", "Nome", "Host", "Porta", "Protocollo")
conn_list = ttk.Treeview(center, columns=cols, show="headings", selectmode="browse")
for col in cols:
    conn_list.heading(col, text=col)
    if col == "Stato": conn_list.column(col, width=60, anchor="center")
    else: conn_list.column(col, width=160 if col!="Porta" else 80, anchor="w")
conn_list.tag_configure("up", foreground="#22c55e")
conn_list.tag_configure("down", foreground="#ef4444")
conn_list.tag_configure("unknown", foreground="#9ca3af")

# Vista icone
icon_scroll = ttk.Scrollbar(center, orient="vertical")
icon_canvas = tk.Canvas(center, highlightthickness=0, yscrollcommand=icon_scroll.set, bg=BG_MAIN, bd=0, relief="flat")
icon_scroll.config(command=icon_canvas.yview)
icon_holder = ttk.Frame(icon_canvas)
icon_canvas_frame = icon_canvas.create_window((0,0), window=icon_holder, anchor="nw")
def _on_icon_configure(event): icon_canvas.itemconfig(icon_canvas_frame, width=icon_canvas.winfo_width())
icon_holder.bind("<Configure>", lambda e: icon_canvas.configure(scrollregion=icon_canvas.bbox("all")))
icon_canvas.bind("<Configure>", _on_icon_configure)

# Icona collegamento
def load_conn_icon(target_px=24):
    try:
        img = tk.PhotoImage(file=CONN_ICON_PATH); w = img.width()
        if w > target_px:
            factor = max(w // target_px, 1); img_small = img.subsample(factor, factor); img._orig = img; return img_small
        return img
    except Exception: return None
conn_icon_small = load_conn_icon(24)

def make_monitor_icon(parent, scale=0.6):
    w, h = int(48*scale), int(36*scale)
    c = tk.Canvas(parent, width=w, height=h, highlightthickness=0, bg=TILE_BG)
    c.create_rectangle(int(4*scale), int(4*scale), int(44*scale), int(26*scale), fill="#2b60a8", outline="#1e3f6e")
    c.create_rectangle(int(4*scale), int(26*scale), int(44*scale), int(28*scale), fill="#1e3f6e", outline="#1e3f6e")
    c.create_rectangle(int(22*scale), int(28*scale), int(26*scale), fill="#666", outline="#444")
    c.create_rectangle(int(16*scale), int(32*scale), int(32*scale), fill="#777", outline="#555")
    return c

# Helpers selezione/icona
def set_tile_bg(tile, color):
    if not tile: return
    try:
        if not tile.winfo_exists(): return
        tile.configure(bg=color)
        for ch in tile.winfo_children():
            try:
                if ch.winfo_exists(): ch.configure(bg=color)
            except tk.TclError: pass
    except tk.TclError: pass

def select_tile(tile, conn_dict):
    global selected_tile, selected_conn
    if selected_tile is not None and selected_tile is not tile:
        try:
            if selected_tile.winfo_exists(): selected_tile._selected=False; set_tile_bg(selected_tile, TILE_BG)
        except Exception: pass
    selected_tile = tile; selected_conn = conn_dict
    if tile and tile.winfo_exists(): tile._selected=True; set_tile_bg(tile, TILE_SELECTED)
    try: conn_list.selection_remove(conn_list.selection())
    except Exception: pass

def bind_recursive(widget, sequence, func):
    widget.bind(sequence, func, add="+")
    for child in widget.winfo_children(): bind_recursive(child, sequence, func)

# Ricerca + viste
def _match_query(conn, q):
    if not q: return True
    q = q.lower()
    return any(str(conn.get(k,"")).lower().find(q) >= 0 for k in ("name","host","protocol"))

def _get_items_filtered():
    q = filter_var.get().strip().lower()
    items = [c for sec in sections for c in sections[sec]] if current_section=="Tutte" else list(sections.get(current_section, []))
    return [c for c in items if _match_query(c, q)]

def set_view_mode(mode):
    view_mode.set(mode)
    for w in center.winfo_children(): w.pack_forget()
    if mode == "lista":
        conn_list.pack(expand=True, fill="both"); refresh_connections()
    else:
        icon_canvas.pack(side="left", expand=True, fill="both"); icon_scroll.pack(side="right", fill="y"); render_icons()

def refresh_connections():
    if view_mode.get() == "lista":
        conn_list.delete(*conn_list.get_children())
        for conn in _get_items_filtered():
            host = conn.get("host","")
            status = _ping_cache.get(host, (None, 0))[0]
            bullet = "●"; tag = "up" if status is True else ("down" if status is False else "unknown")
            conn_list.insert("", "end",
                values=(bullet, conn.get("name",""), conn.get("host",""),
                        conn.get("port",""), conn.get("protocol","")),
                tags=(tag,))
            if _need_ping(host):
                def _update_row(h=host): refresh_connections()
                _ping_async(host, on_done=lambda h, st: _update_row(h))
    else:
        render_icons()

def _attach_led_to_tile(led_lbl: tk.Label, conn: dict):
    host = conn.get("host","")
    status = _ping_cache.get(host, (None, 0))[0]; _set_led_color(led_lbl, status)
    if _need_ping(host): _ping_async(host, on_done=lambda h, st: _set_led_color(led_lbl, st))

def render_icons():
    global selected_tile, selected_conn, _mouse_down_tile, _drag_start_xy, _drag_active
    selected_tile=None; selected_conn=None; _mouse_down_tile=None; _drag_start_xy=None; _drag_active=False
    for w in icon_holder.winfo_children(): w.destroy()

    items = _get_items_filtered()
    cols, pad = 5, 4
    for idx, conn in enumerate(items):
        r, c = idx // cols, idx % cols
        tile = tk.Frame(icon_holder, bd=1, relief="ridge", bg=TILE_BG)
        tile.grid(row=r, column=c, padx=pad, pady=pad, sticky="nsew"); tile._selected=False

        header = tk.Frame(tile, bg=TILE_BG); header.pack(fill="x", padx=6, pady=(6,2))
        led = tk.Label(header, text="●", font=("Segoe UI", 9, "bold"), bg=TILE_BG); led.pack(side="left", padx=(0,6))
        tk.Label(header, text=conn.get("name",""), font=BOLD_FONT, bg=TILE_BG).pack(side="left", anchor="w")

        if conn_icon_small: tk.Label(tile, image=conn_icon_small, bg=TILE_BG).pack(pady=(0,2))
        else: make_monitor_icon(tile, 0.6).pack(pady=(0,2))

        tk.Label(tile, text=f"{conn.get('host','')}:{conn.get('port','')}", fg="#555", bg=TILE_BG).pack()
        tk.Label(tile, text=f"{conn.get('protocol','')}", fg="#777", bg=TILE_BG).pack()

        btns = ttk.Frame(tile); btns.pack(pady=(4,8))
        ttk.Button(btns, text="Apri", width=7, command=lambda c=conn: connect_connection_dict(c)).pack(side="left")
        ttk.Button(btns, text="✏",  width=3, command=lambda c=conn, t=tile: (select_tile(t, c), add_connection_popup(True))).pack(side="left", padx=3)
        ttk.Button(btns, text="🗑",  width=3, command=lambda c=conn: remove_connection_from_dict(c)).pack(side="left")

        values_tuple = (conn.get("name",""), conn.get("host",""), conn.get("port",""), conn.get("protocol",""))
        def on_press(e, t=tile, v=values_tuple, cdict=conn):
            select_tile(t, cdict)
            global _drag_start_xy, _drag_active, _mouse_down_tile, drag_data
            _drag_start_xy = root.winfo_pointerxy(); _drag_active=False; _mouse_down_tile=t
            conn_found, sec = find_connection_by_values(v)
            drag_data["values"]=v; drag_data["from_section"]=sec; drag_data["source"]="icon"
        def on_motion(e): _maybe_start_drag()
        def on_release(e):
            global _mouse_down_tile
            if _drag_active: perform_drop()
            else: drag_data["values"]=None; drag_data["from_section"]=None; drag_data["source"]=None
            _mouse_down_tile=None
        def on_enter(e, t=tile):
            set_tile_bg(t, TILE_SELECTED if getattr(t,"_selected",False) or (_mouse_down_tile is t) else TILE_HOVER)
        def on_leave(e, t=tile):
            set_tile_bg(t, TILE_SELECTED if getattr(t,"_selected",False) or (_mouse_down_tile is t) else TILE_BG)

        bind_recursive(tile, "<Button-1>", on_press)
        bind_recursive(tile, "<B1-Motion>", lambda e: on_motion(e))
        bind_recursive(tile, "<ButtonRelease-1>", lambda e: on_release(e))
        bind_recursive(tile, "<Enter>", on_enter); bind_recursive(tile, "<Leave>", on_leave)
        tile.bind("<Double-Button-1>", lambda e, cdict=conn: connect_connection_dict(cdict))

        _attach_led_to_tile(led, conn)

    for i in range(cols): icon_holder.grid_columnconfigure(i, weight=1)

# Bind lista per DnD e ordinamento semplice
def _list_start(e):
    sel = conn_list.focus()
    if not sel: return
    values = tuple(conn_list.item(sel)["values"][1:])
    global _drag_start_xy, _drag_active
    _drag_start_xy = root.winfo_pointerxy(); _drag_active=False
    conn, sec = find_connection_by_values(values)
    drag_data["values"]=values; drag_data["from_section"]=sec; drag_data["source"]="list"

def _sort_treeview(tree, col, reverse=False):
    data = [(tree.set(k, col), k) for k in tree.get_children("")]
    if col == "Porta": data.sort(key=lambda t: int(t[0]) if str(t[0]).isdigit() else 0, reverse=reverse)
    else: data.sort(key=lambda t: str(t[0]).lower(), reverse=reverse)
    for i, (_, k) in enumerate(data): tree.move(k, "", i)
    tree.heading(col, command=lambda: _sort_treeview(tree, col, not reverse))
for col in cols: conn_list.heading(col, text=col, command=lambda c=col: _sort_treeview(conn_list, c))

conn_list.bind("<Button-1>", _list_start)
conn_list.bind("<B1-Motion>", lambda e: _maybe_start_drag())
conn_list.bind("<ButtonRelease-1>", lambda e: perform_drop())
section_list.bind("<ButtonRelease-1>", lambda e: perform_drop())
root.bind("<Motion>", on_global_mouse_move)

# Cambio sezione
def on_section_select(event):
    global current_section
    sel = section_list.focus()
    if sel:
        current_section = section_list.item(sel)["text"]; refresh_connections()
section_list.bind("<<TreeviewSelect>>", on_section_select)

# CRUD cartelle
def add_section_popup():
    def add():
        name = entry.get().strip()
        if name and name not in sections:
            sections[name]=[]
            if name not in section_order:
                if section_order and section_order[0]=="Tutte": section_order.insert(1, name)
                else: section_order.append(name)
            refresh_sections_tree(); save_connections(); popup.destroy(); refresh_connections(); log(f"Folder created: {name}")
        else:
            messagebox.showwarning("Attenzione","Nome cartella non valido o già esistente.")
    popup = tk.Toplevel(root); popup.title("Nuova Cartella")
    tk.Label(popup, text="Nome cartella:").pack(padx=10, pady=5)
    entry = tk.Entry(popup); entry.pack(padx=10, pady=5)
    tk.Button(popup, text="Aggiungi", command=add).pack(padx=10, pady=10); entry.focus()

def rename_section_popup():
    sel = section_list.focus()
    if not sel: return messagebox.showwarning("Attenzione","Seleziona una cartella.")
    old = section_list.item(sel)["text"]
    if old=="Tutte": return messagebox.showwarning("Attenzione","Non puoi rinominare 'Tutte'.")
    def do_rename():
        new = entry.get().strip()
        if not new or new in sections: return messagebox.showwarning("Attenzione","Nome non valido o già esistente.")
        sections[new]=sections.pop(old)
        for i,s in enumerate(section_order):
            if s==old: section_order[i]=new; break
        refresh_sections_tree(); save_connections(); popup.destroy(); refresh_connections(); log(f"Folder renamed: {old} -> {new}")
    popup = tk.Toplevel(root); popup.title("Rinomina Cartella")
    tk.Label(popup, text=f"Nuovo nome per '{old}':").pack(padx=10, pady=5)
    entry = tk.Entry(popup); entry.insert(0, old); entry.pack(padx=10, pady=5)
    tk.Button(popup, text="Rinomina", command=do_rename).pack(padx=10, pady=10); entry.focus()

def remove_section():
    global current_section
    sel = section_list.focus()
    if sel:
        name = section_list.item(sel)["text"]
        if name=="Tutte": return messagebox.showerror("Errore","Non puoi eliminare 'Tutte'")
        if not messagebox.askyesno("Conferma", f"Eliminare la cartella '{name}' e le connessioni al suo interno?"): return
        if name in sections: del sections[name]
        try: section_order.remove(name)
        except ValueError: pass
        refresh_sections_tree(); current_section="Tutte"; refresh_connections(); save_connections(); log(f"Folder removed: {name}")

# CRUD connessioni (con password VNC di default + RDP username)
def add_connection_popup(edit=False):
    if current_section=="Tutte" and not edit:
        return messagebox.showwarning("Attenzione","Seleziona una cartella specifica prima di aggiungere.")
    sel_values = None
    existing = None
    existing_sec = None
    if edit:
        sel = conn_list.focus()
        if not sel and not selected_conn:
            return messagebox.showwarning("Attenzione","Seleziona una connessione da modificare.")
        sel_values = conn_list.item(sel)["values"][1:] if sel else (
            selected_conn.get("name",""), selected_conn.get("host",""),
            selected_conn.get("port",""), selected_conn.get("protocol","")
        )
        existing, existing_sec = find_connection_by_values(sel_values)

    def on_proto_change(_=None):
        p = combo_proto.get()
        entry_port.configure(state="normal")
        if p == "VNC":
            pw_row.grid()
            rdp_row.grid_remove()
            if not edit and not entry_password.get():
                entry_password.insert(0, "500rossa")  # default
            if not entry_port.get().strip():
                entry_port.insert(0, "5900")
        elif p == "RDP":
            pw_row.grid_remove()
            rdp_row.grid(row=5, column=0, columnspan=2, sticky="w")  # mostra campo utente
            entry_port.delete(0,"end"); entry_port.insert(0,"3389")
        else:
            pw_row.grid_remove()
            rdp_row.grid_remove()
            entry_port.delete(0,"end"); entry_port.insert(0,"22")

    def save_conn():
        name=entry_name.get().strip()
        host=entry_host.get().strip()
        proto=combo_proto.get().strip().upper()
        port_input=entry_port.get().strip()
        vnc_password = entry_password.get().strip() if proto=="VNC" else ""
        rdp_user = entry_rdp_user.get().strip() if proto=="RDP" else ""

        try:
            port_int = int(port_input) if port_input else (5900 if proto=="VNC" else (3389 if proto=="RDP" else 22))
        except:
            return messagebox.showerror("Errore","Porta deve essere un numero")

        if name and host and proto in ["VNC","RDP","SSH"]:
            conn_payload = {"name":name,"host":host,"port":port_int,"protocol":proto}
            if proto == "VNC":
                if not edit and not vnc_password:
                    vnc_password = "500rossa"
                conn_payload["password"] = vnc_password
            elif proto == "RDP":
                if rdp_user:
                    conn_payload["username"] = rdp_user

            if edit and existing and existing_sec:
                # pulisci/aggiorna chiavi specifiche protocollo
                if existing.get("password") and proto!="VNC":
                    existing.pop("password", None)
                if existing.get("username") and proto!="RDP":
                    existing.pop("username", None)
                existing.update(conn_payload)
                log(f"Edited connection: {name} ({host}:{port_int} {proto})")
            else:
                sections.setdefault(current_section, []).append(conn_payload)
                log(f"Created connection: {name} in '{current_section}' ({host}:{port_int} {proto})")
            refresh_connections(); save_connections(); popup.destroy()
        else:
            messagebox.showerror("Errore","Compila tutti i campi correttamente")

    popup = tk.Toplevel(root); popup.title("Modifica Connessione" if edit else "Nuova Connessione")
    frm = ttk.Frame(popup, padding=10); frm.pack(fill="both", expand=True)
    ttk.Label(frm, text="Nome:").grid(row=0,column=0,sticky="w",pady=3); entry_name=ttk.Entry(frm,width=32); entry_name.grid(row=0,column=1,sticky="ew",pady=3)
    ttk.Label(frm, text="Host/IP:").grid(row=1,column=0,sticky="w",pady=3); entry_host=ttk.Entry(frm,width=32); entry_host.grid(row=1,column=1,sticky="ew",pady=3)
    ttk.Label(frm, text="Porta:").grid(row=2,column=0,sticky="w",pady=3); entry_port=ttk.Entry(frm,width=10); entry_port.grid(row=2,column=1,sticky="w",pady=3)
    ttk.Label(frm, text="Protocollo:").grid(row=3,column=0,sticky="w",pady=3)
    combo_proto=ttk.Combobox(frm, values=["VNC","RDP","SSH"], state="readonly", width=8); combo_proto.set("VNC"); combo_proto.grid(row=3,column=1,sticky="w",pady=3)

    # VNC password row
    pw_row = ttk.Frame(frm)
    ttk.Label(pw_row, text="Password VNC:").grid(row=0, column=0, sticky="w", pady=3)
    entry_password = ttk.Entry(pw_row, width=20, show="*"); entry_password.grid(row=0, column=1, sticky="w", pady=3)
    pw_row.grid(row=4, column=0, columnspan=2, sticky="w")

    # RDP username row (visibile solo se RDP)
    rdp_row = ttk.Frame(frm)
    ttk.Label(rdp_row, text="Nome utente (dominio\\utente):").grid(row=0, column=0, sticky="w", pady=3)
    entry_rdp_user = ttk.Entry(rdp_row, width=28)
    entry_rdp_user.grid(row=0, column=1, sticky="w", pady=3)

    frm.grid_columnconfigure(1,weight=1); combo_proto.bind("<<ComboboxSelected>>", on_proto_change)

    # Pulsanti (spostati giù di una riga per lasciare spazio a rdp_row=5)
    btns=ttk.Frame(frm); btns.grid(row=6,column=0,columnspan=2,pady=(8,0))
    ttk.Button(btns,text="Salva",command=save_conn).pack(side="left",padx=4)
    ttk.Button(btns,text="Annulla",command=popup.destroy).pack(side="left")

    # Precarica in edit
    if edit and existing:
        entry_name.insert(0, existing.get("name",""))
        entry_host.insert(0, existing.get("host",""))
        entry_port.insert(0, existing.get("port",""))
        combo_proto.set(existing.get("protocol","VNC"))
        if existing.get("protocol","").upper()=="VNC":
            pw_row.grid(); entry_password.insert(0, existing.get("password",""))
        elif existing.get("protocol","").upper()=="RDP":
            rdp_row.grid(row=5, column=0, columnspan=2, sticky="w")
            entry_rdp_user.insert(0, existing.get("username",""))
    else:
        entry_port.insert(0, "5900")

    on_proto_change()
    entry_name.focus()

def remove_connection_from_dict(conn):
    for sec, lst in sections.items():
        if conn in lst:
            if not messagebox.askyesno("Conferma", f"Eliminare '{conn.get('name')}'?"): return
            lst.remove(conn); save_connections(); refresh_connections(); log(f"Removed connection: {conn.get('name')}"); return

def remove_connection():
    sel = conn_list.focus()
    if sel:
        values = conn_list.item(sel)["values"][1:]
        conn, sec = find_connection_by_values(values)
        if conn and sec:
            if not messagebox.askyesno("Conferma", f"Eliminare '{conn.get('name')}'?"): return
            sections[sec].remove(conn); refresh_connections(); save_connections(); log(f"Removed connection: {conn.get('name')}")

# ───────── Helper RDP: crea .rdp e lancia mstsc ─────────
def _launch_rdp_with_rdpfile(host: str, port: int | str, username: str | None):
    """Crea un .rdp temporaneo con host:port e (se presente) username, poi apre mstsc su quel file."""
    try:
        port = int(port)
    except Exception:
        port = 3389

    lines = [
        f"full address:s:{host}:{port}",
        f"prompt for credentials:i:1",
        "authentication level:i:2",
        "negotiate security layer:i:1",
        "enablecredsspsupport:i:1",
        "disableconnectionsharing:i:0",
        "autoreconnection enabled:i:1",
        "bandwidthautodetect:i:1",
    ]
    if username:
        lines.append(f"username:s:{username}")

    rdp_path = os.path.join(APPDATA_DIR, "last.rdp")
    try:
        with open(rdp_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception as e:
        messagebox.showerror("Errore RDP", f"Impossibile scrivere il file RDP:\n{e}")
        return

    try:
        subprocess.Popen(["mstsc", rdp_path])
        log(f"Opened RDP via .rdp file: {host}:{port} (user={username or 'N/D'})")
    except Exception as e:
        messagebox.showerror("Errore RDP", f"Impossibile avviare mstsc:\n{e}")

# Connetti
def connect_connection_dict(cdict):
    host = cdict.get("host",""); port = cdict.get("port",""); proto = cdict.get("protocol","").upper()
    try:
        if proto=="VNC":
            if os.path.exists(VNC_VIEWER):
                args = [VNC_VIEWER]
                if cdict.get("password"): args.append(f"-password={cdict.get('password')}")
                args.append(f"{host}::{port}")
                subprocess.Popen(args)
                log(f"Opened VNC to {host}:{port} (with_password={bool(cdict.get('password'))})")
            else:
                messagebox.showerror("Errore","Viewer VNC non trovato!")
        elif proto=="RDP":
            username = (cdict.get("username", "") or "").strip()
            _launch_rdp_with_rdpfile(host, port, username)  # soluzione robusta
        elif proto=="SSH":
            subprocess.Popen(["putty", host, "-P", str(port)]); log(f"Opened SSH to {host}:{port}")
        else:
            messagebox.showerror("Errore", f"Protocollo {proto} non supportato.")
    except Exception as e:
        messagebox.showerror("Errore", f"Impossibile avviare la connessione:\n{e}")

def connect_to_selected():
    sel = conn_list.focus()
    if sel:
        name, host, port, proto = conn_list.item(sel)["values"][1:]
        conn, _ = find_connection_by_values((name, host, port, proto))
        if conn: connect_connection_dict(conn); return
    if selected_conn: connect_connection_dict(selected_conn); return
    messagebox.showwarning("Selezione mancante", "Seleziona una connessione dalla lista o dalle icone.")

# ───────── VNC password encode (unica definizione, usata sia locale che remoto) ─────────
def vnc_encode_password(raw_password: str) -> bytes | None:
    if DES is None: return None
    key = bytes([0x23,0x52,0x6B,0x06,0x23,0x4E,0x58,0x07])
    def bitrev(b): v=b; v=((v&0xF0)>>4)|((v&0x0F)<<4); v=((v&0xCC)>>2)|((v&0x33)<<2); v=((v&0xAA)>>1)|((v&0x55)<<1); return v
    key = bytes(bitrev(b) for b in key)
    pw = (raw_password or "")[:8].encode("latin1","ignore").ljust(8, b"\x00")
    return DES.new(key, DES.MODE_ECB).encrypt(pw)

# ───────── Avvio TightVNC locale con form password/porta (HKCU) ─────────
def _reg_set_hkcu_tvn_str_or_bin(name: str, value, regtype):
    if not winreg:
        return False
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\TightVNC\Server") as k:
            winreg.SetValueEx(k, name, 0, regtype, value)
        return True
    except Exception:
        return False

def _reg_set_hkcu_tvn_dword(name: str, d: int) -> bool:
    return _reg_set_hkcu_tvn_str_or_bin(name, int(d), winreg.REG_DWORD)

def _reg_set_hkcu_tvn_password(raw_password: str) -> bool:
    if DES is None or not winreg:
        return False
    enc = vnc_encode_password(raw_password)
    if not enc:
        return False
    return _reg_set_hkcu_tvn_str_or_bin("Password", enc, winreg.REG_BINARY)

def _reg_get_hkcu_tvn_dword(name: str, default=None):
    if not winreg: return default
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\TightVNC\Server") as k:
            val, typ = winreg.QueryValueEx(k, name)
            if typ == winreg.REG_DWORD:
                return int(val)
    except Exception:
        pass
    return default

def open_firewall_port(port_int):
    try:
        run_silent(["netsh","advfirewall","firewall","add","rule",
                    f"name=HubVNC {port_int}","dir=in","action=allow",
                    "protocol=TCP",f"localport={port_int}"],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def start_vnc_server():
    popup = tk.Toplevel(root)
    popup.title("Avvia Server VNC")
    popup.resizable(False, False)
    frm = ttk.Frame(popup, padding=12)
    frm.pack(fill="both", expand=True)

    pw_var = tk.StringVar()
    pw2_var = tk.StringVar()
    existing_port = _reg_get_hkcu_tvn_dword("RfbPort", 5900)
    port_var = tk.StringVar(value=str(existing_port))
    fw_var = tk.BooleanVar(value=True)

    ttk.Label(frm, text="Password (max 8):").grid(row=0, column=0, sticky="w", pady=4)
    e1 = ttk.Entry(frm, textvariable=pw_var, show="*"); e1.grid(row=0, column=1, sticky="ew", pady=4)

    ttk.Label(frm, text="Conferma password:").grid(row=1, column=0, sticky="w", pady=4)
    e2 = ttk.Entry(frm, textvariable=pw2_var, show="*"); e2.grid(row=1, column=1, sticky="ew", pady=4)

    ttk.Label(frm, text="Porta TCP:").grid(row=2, column=0, sticky="w", pady=4)
    e3 = ttk.Entry(frm, textvariable=port_var, width=8); e3.grid(row=2, column=1, sticky="w", pady=4)

    ttk.Checkbutton(frm, text="Apri firewall su questa porta", variable=fw_var).grid(row=3, column=0, columnspan=2, sticky="w", pady=(2,8))

    btns = ttk.Frame(frm); btns.grid(row=4, column=0, columnspan=2, sticky="e")
    def do_cancel(): popup.destroy()
    def do_start():
        pw = pw_var.get()
        pw2 = pw2_var.get()
        port_txt = port_var.get().strip()

        if pw != pw2:
            messagebox.showerror("Errore", "Le password non coincidono."); return
        if len(pw) > 8:
            messagebox.showerror("Errore", "La password VNC deve essere lunga al massimo 8 caratteri."); return
        try:
            port_int = int(port_txt)
            if not (1 <= port_int <= 65535): raise ValueError()
        except Exception:
            messagebox.showerror("Errore", "Porta non valida."); return

        wrote_pw = True
        if pw:
            if DES is None:
                wrote_pw = False
                messagebox.showwarning("Avviso", "pycryptodome non installato: impossibile impostare la password.\n"
                                                  "Installalo con:  pip install pycryptodome")
            else:
                wrote_pw = _reg_set_hkcu_tvn_password(pw)
        else:
            wrote_pw = True

        _reg_set_hkcu_tvn_dword("UseVncAuthentication", 1)
        _reg_set_hkcu_tvn_dword("AcceptRfbConnections", 1)
        _reg_set_hkcu_tvn_dword("AlwaysShared", 1)
        _reg_set_hkcu_tvn_dword("LoopbackOnly", 0)
        _reg_set_hkcu_tvn_dword("RfbPort", port_int)

        if not os.path.exists(VNC_SERVER):
            messagebox.showerror("Errore", "tvnserver.exe non trovato nella cartella dell'app."); return
        try:
            popen_silent([VNC_SERVER, "-run"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); log("Started TightVNC Server (manual)")
        except Exception as e:
            messagebox.showerror("Errore", f"Impossibile avviare TightVNC Server:\n{e}"); return

        if fw_var.get():
            open_firewall_port(port_int)

        info_pw = "Password impostata." if (pw and wrote_pw) else ("SENZA password." if not pw else "Password NON impostata dall'app.")
        messagebox.showinfo("Server VNC avviato", f"{info_pw}\nPorta: {port_int}\nOra puoi collegarti da remoto con IP::{port_int}.")
        popup.destroy()

    ttk.Button(btns, text="Annulla", command=do_cancel).pack(side="right", padx=(6,0))
    ttk.Button(btns, text="Avvia", command=do_start).pack(side="right")

    frm.grid_columnconfigure(1, weight=1)
    e1.focus_set()
    popup.transient(root); popup.grab_set()

# ───────── Reset password VNC REMOTO (HKLM + riavvio servizio) — ASINCRONO ─────────
def _connect_remote_hklm(host:str):
    if not winreg: raise RuntimeError("API registro non disponibili.")
    return winreg.ConnectRegistry(rf"\\{host}", winreg.HKEY_LOCAL_MACHINE)

def _write_tightvnc_pw_hklm(reg, enc:bytes)->str:
    paths=[r"Software\TightVNC\Server", r"Software\WOW6432Node\TightVNC\Server"]
    last=None
    for p in paths:
        try:
            k=winreg.CreateKey(reg,p)
            winreg.SetValueEx(k,"Password",0,winreg.REG_BINARY,enc)
            winreg.CloseKey(k)
            return p
        except Exception as e: last=e
    raise last if last else RuntimeError("Impossibile scrivere la password in HKLM.")

# --- utility extra per RPC / SC timeout e messaggi chiari ---
def _winerr(e):
    try:
        return int(getattr(e, "winerror", 0)) or int(getattr(e, "errno", 0))
    except Exception:
        return 0

def _explain_rpc_error(e):
    code = _winerr(e)
    if code in (1722,):  # RPC_S_SERVER_UNAVAILABLE
        return ("Impossibile contattare il server RPC.\n"
                "- Host spento o non raggiungibile\n"
                "- Firewall blocca RPC/SMB\n"
                "- Remote Registry non disponibile")
    if code in (1723,):  # RPC_S_SERVER_TOO_BUSY
        return ("Il server RPC è occupato/intasato.\n"
                "- Riprova tra poco\n"
                "- Verifica carico del PC remoto\n"
                "- Controlla rete/firewall")
    if code in (1727,):  # RPC_S_CALL_FAILED
        return ("Chiamata RPC fallita.\n"
                "Verifica connettività, permessi amministrativi e Remote Registry.")
    return f"Errore Windows {code}."

def _run_sc(host, args, timeout=20):
    # Esegue 'sc \\host <args>' con timeout; ritorna (rc, stdout)
    try:
        p = subprocess.run(["sc", f"\\\\{host}"] + args,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, **_silent_flags_kwargs(), timeout=timeout)
        return p.returncode, (p.stdout or "")
    except subprocess.TimeoutExpired:
        return 1, "Timeout SC."
    except Exception as e:
        return 1, f"Errore SC: {e}"

def _wait_service_state(host, svc, desired, timeout=25, poll_every=1.0):
    # desired: "RUNNING" o "STOPPED"
    t0 = time.time()
    while time.time() - t0 < timeout:
        rc, out = _run_sc(host, ["query", svc], timeout=8)
        if rc == 0 and f"STATE" in out and desired in out:
            return True
        time.sleep(poll_every)
    return False

def _check_remote_registry(host):
    # Prova connessione + chiusura subito: se fallisce, lancia eccezione
    if not winreg:
        raise RuntimeError("API registro non disponibili su questo sistema.")
    try:
        reg = winreg.ConnectRegistry(rf"\\{host}", winreg.HKEY_LOCAL_MACHINE)
        try: reg.Close()
        except Exception: pass
        return True
    except OSError as e:
        msg = _explain_rpc_error(e)
        raise RuntimeError(f"Connessione al Registro remoto fallita.\n{msg}") from e

def reset_vnc_password_remote():
    if not is_admin:
        messagebox.showerror("Admin richiesto", "Questa funzione è disponibile solo in modalità Admin.\nVai su Impostazioni → Accedi come Admin…")
        return
    if DES is None:
        messagebox.showerror("Errore","Installa pycryptodome")
        return

    host = simpledialog.askstring("Reset Password VNC Remoto","Host remoto:")
    if not host: return
    pw = simpledialog.askstring("Reset Password VNC Remoto","Nuova password (max 8):", show="*")
    if not pw: return
    if len(pw) > 8:
        messagebox.showerror("Errore", "La password VNC deve essere lunga al massimo 8 caratteri.")
        return

    # Finestra di progresso non bloccante
    prog = tk.Toplevel(root)
    prog.title("Reset in corso…")
    prog.resizable(False, False)
    frm = ttk.Frame(prog, padding=12); frm.pack(fill="both", expand=True)
    ttk.Label(frm, text=f"Host: {host}\nOperazione in corso…").pack(pady=6)
    bar = ttk.Progressbar(frm, mode="indeterminate", length=260)
    bar.pack(pady=6); bar.start(10)
    prog.transient(root); prog.grab_set()
    try:
        prog.attributes("-topmost", True)
    except Exception:
        pass

    result = {"ok": False, "msg": "", "detail": ""}

    def finish():
        try:
            bar.stop()
            prog.destroy()
        except Exception:
            pass
        if result["ok"]:
            messagebox.showinfo("Reset Password", result["msg"] + ("\n\n" + result["detail"] if result["detail"] else ""))
            log(f"Remote VNC password reset on {host} (ok)")
        else:
            messagebox.showerror("Errore", result["msg"] + ("\n\nDettagli:\n" + result["detail"] if result["detail"] else ""))
            log(f"Remote VNC password reset on {host} FAILED: {result['detail']}")

    def worker():
        try:
            # 1) ping rapido (solo avviso se non risponde)
            try:
                if _ping_host_once(host, 800) is False:
                    result["detail"] += "Avviso: host non risponde al ping.\n"
            except Exception:
                pass

            # 2) test Remote Registry
            _check_remote_registry(host)

            # 3) codifica password e scrittura HKLM remoto
            enc = vnc_encode_password(pw)
            if not enc:
                raise RuntimeError("Codifica password fallita (pycryptodome).")
            reg = _connect_remote_hklm(host)
            path_used = _write_tightvnc_pw_hklm(reg, enc)
            try: reg.Close()
            except Exception: pass

            # 4) riavvio servizio con polling e timeout
            restarted = False
            for svc in ["tvnserver", "TightVNC Server"]:
                _run_sc(host, ["stop", svc], timeout=15)
                _wait_service_state(host, svc, "STOPPED", timeout=20)
                rc, _ = _run_sc(host, ["start", svc], timeout=20)
                if rc == 0 and _wait_service_state(host, svc, "RUNNING", timeout=25):
                    restarted = True
                    break

            result["ok"] = True
            result["msg"] = (f"Password aggiornata su {host}\n"
                             f"Chiave: HKLM\\{path_used}\n"
                             f"Servizio riavviato: {'SÌ' if restarted else 'NO'}")
        except OSError as e:
            result["msg"] = "Impossibile completare il reset."
            result["detail"] += _explain_rpc_error(e)
        except Exception as e:
            result["msg"] = "Impossibile completare il reset."
            result["detail"] += str(e)

        root.after(0, finish)

    threading.Thread(target=worker, daemon=True).start()

# Statusbar
hostname, user, osver, ip = get_pc_info()
status = ttk.Label(root, anchor="w", relief="groove",
                   text=f" Utente: {user}  •  PC: {hostname}  •  IP: {ip}  •  OS: {osver}")
status.grid(row=1, column=0, sticky="ew", padx=8, pady=(0,8))

# Eventi generali
root.bind("<Control-n>", lambda e: add_connection_popup(False))
root.bind("<Control-e>", lambda e: add_connection_popup(True))
root.bind("<Delete>",    lambda e: remove_connection())
root.bind("<Return>",    lambda e: connect_to_selected())

# Context menu su lista/icone
ctx = tk.Menu(root, tearoff=0)
ctx.add_command(label="Connetti", command=lambda: connect_to_selected())
ctx.add_command(label="Modifica", command=lambda: add_connection_popup(edit=True))
ctx.add_command(label="Rimuovi",  command=lambda: remove_connection())
icon_holder.bind("<Button-3>", lambda e: (ctx.tk_popup(e.x_root, e.y_root), ctx.grab_release()))
conn_list.bind("<Button-3>",   lambda e: (ctx.tk_popup(e.x_root, e.y_root), ctx.grab_release()))

# ───────── Autostart VNC all'avvio app ─────────
def _autostart_vnc_if_enabled():
    if not AUTOSTART_VNC_ON_LAUNCH:
        return
    if not os.path.exists(VNC_SERVER):
        return
    try:
        popen_silent([VNC_SERVER, "-run"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port = _reg_get_hkcu_tvn_dword("RfbPort", None)
        if port:
            open_firewall_port(int(port))
        log("Started TightVNC Server (autostart)")
    except Exception:
        pass

# Avvio UI
def initial_section_focus():
    for iid in section_list.get_children(""):
        if section_list.item(iid)["text"] == "Tutte":
            section_list.selection_set(iid); section_list.focus(iid); break

def main():
    log("HubVNC started")
    initial_section_focus()
    set_view_mode("icone")
    refresh_connections()
    update_admin_visibility()  # applica visibilità iniziale funzioni admin (FORZATO)
    try:
        filter_var.trace_add("write", lambda *args: refresh_connections())
    except Exception:
        pass
    _autostart_vnc_if_enabled()
    # sincronizza vista checkbox con stato reale del registro (se diverso da settings)
    if settings.get("autostart_app", False) != is_autostart_enabled():
        settings["autostart_app"] = is_autostart_enabled()
        save_settings(settings)
    root.mainloop()
    log("HubVNC closed")

if __name__ == "__main__":
    main()
