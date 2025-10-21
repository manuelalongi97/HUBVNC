# HubVNC.py — UI stile RealVNC con cartelle+sottocartelle (Tree), DnD connessioni, vista Lista/Icone,
# LED ping (verde/rosso), avvio TightVNC con form password/porta (HKCU), reset password VNC remoto (HKLM) ASINCRONO,
# statusbar e logging. Extra: default password VNC "500rossa", ping silenzioso (no finestre),
# autostart TightVNC all'avvio app, AUTO-AVVIO dell’APP (HKCU\Run), check aggiornamenti GitHub (script),
# Quick "VNC/RDP/SSH".
#
# NOTE:
# - La password VNC delle connessioni è salvata in chiaro in connections.json per semplicità.
# Tested su Python 3.11/3.12/3.13 su Windows.

VERSION = "1.5.0"

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import subprocess, os, sys, json, shutil, getpass, socket, threading, time, webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

# Networking per "Check for updates"
import ssl, urllib.request, urllib.error
import traceback

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

# GitHub Update
GITHUB_REPO = "Lyoneega/HubVNC"
RAW_BRANCH  = "main"
RAW_FILENAME = os.path.basename(__file__) if not getattr(sys, "frozen", False) else "App.py"
VERSION_FILE_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{RAW_BRANCH}/VERSION.txt"
RAW_SCRIPT_URL   = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{RAW_BRANCH}/{RAW_FILENAME}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
REPO_RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"

# App dirs
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
    RESOURCE_DIR = sys._MEIPASS
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    RESOURCE_DIR = APP_DIR

APPDATA_DIR = os.path.join(os.environ.get("APPDATA", APP_DIR), "HubVNC")
os.makedirs(APPDATA_DIR, exist_ok=True)
SAVE_FILE      = os.path.join(APPDATA_DIR, "connections.json")
SETTINGS_FILE  = os.path.join(APPDATA_DIR, "settings.json")
LOG_FILE       = os.path.join(APPDATA_DIR, "hubvnc.log")

VNC_VIEWER  = os.path.join(RESOURCE_DIR, "tvnviewer.exe")
VNC_SERVER  = os.path.join(RESOURCE_DIR, "tvnserver.exe")
CONN_ICON_PATH = os.path.join(RESOURCE_DIR, "conn_ico.png")

# ───────── logging (con rotazione semplice) ─────────
def log(msg: str):
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 2_000_000:
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            try: shutil.move(LOG_FILE, LOG_FILE + f".{ts}.bak")
            except Exception: pass
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} | {msg}\n")
    except Exception:
        pass

# ───────── settings ─────────
DEFAULT_SETTINGS = {
    "autostart_app": False,
    "admin_enabled": False,
    "admin_password": "",
    "autostart_vnc_on_launch": False,   # C) opt-in
    "did_initial_vnc_setup": False      # setup automatico 1a volta
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
is_admin = False

# ───────── autostart APP (HKCU\Run) ─────────
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "HubVNC"

def _exe_for_autostart():
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
        messagebox.showerror("Errore", "API registro non disponibili.")
        return False
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.SetValueEx(k, RUN_VALUE_NAME, 0, winreg.REG_SZ, _exe_for_autostart())
        log("Enabled app autostart")
        return True
    except Exception as e:
        messagebox.showerror("Errore", f"Impossibile attivare l'avvio automatico:\n{e}")
        return False

def disable_autostart():
    if not winreg:
        messagebox.showerror("Errore", "API registro non disponibili.")
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            try:
                winreg.DeleteValue(k, RUN_VALUE_NAME)
            except FileNotFoundError:
                pass
        log("Disabled app autostart")
        return True
    except Exception as e:
        messagebox.showerror("Errore", f"Impossibile disattivare:\n{e}")
        return False

# ───────── esecuzione silenziosa ─────────
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

# ───────── helper ─────────
def parse_host_and_port(text_host: str, text_port: str, default_port: int):
    h = (text_host or "").strip()
    p = (text_port or "").strip()
    if (":" in h) and (not p):
        if "::" in h:
            h, p = h.split("::", 1)
        else:
            h, p = h.rsplit(":", 1)
    h = h.strip()
    try:
        p_int = int(p) if p else int(default_port)
    except Exception:
        p_int = int(default_port)
    return h, p_int

# ───────── modello dati ─────────
DATA = {
    "folders": ["Tutte"],
    "connections": [],
    "last_vnc_time": {}
}

def _write_default():
    DATA["folders"] = ["Tutte", "Produzione", "Test"]
    DATA["connections"] = []
    DATA["last_vnc_time"] = {}
    with open(SAVE_FILE, "w", encoding="utf-8") as f:
        json.dump(DATA, f, indent=2, ensure_ascii=False)

def _load_data():
    if RESET_ON_START and os.path.exists(SAVE_FILE):
        try: os.remove(SAVE_FILE)
        except Exception: pass
    if not os.path.exists(SAVE_FILE):
        _write_default()
    try:
        with open(SAVE_FILE, "r", encoding="utf-8") as f:
            tmp = json.load(f)
        DATA.clear()
        DATA.update(tmp)
        if "Tutte" not in DATA["folders"]:
            DATA["folders"] = ["Tutte"] + [p for p in DATA["folders"] if p != "Tutte"]
    except Exception:
        _write_default()

def _save_data():
    try:
        with open(SAVE_FILE, "w", encoding="utf-8") as f:
            json.dump(DATA, f, indent=2, ensure_ascii=False)
    except Exception as e:
        messagebox.showerror("Errore", f"Salvataggio fallito:\n{e}")

_load_data()

# ───────── status PC ─────────
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
    try:
        import platform
        osver = f"{platform.system()} {platform.release()}"
    except Exception:
        osver = "N/D"
    ip = get_primary_ipv4()
    return hostname, user, osver, ip

# ───────── UI ─────────
BG_MAIN, BG_SIDEBAR, BG_TILE = "#f5f7fb", "#eef2f7", "#f9fafb"
TILE_BG, TILE_HOVER, TILE_SELECTED = BG_TILE, "#eaf2ff", "#d6e4ff"

root = tk.Tk()
root.title("Hub VNC/RDP/SSH - All-in-One")
root.geometry("1120x660"); root.minsize(940, 560)
root.grid_rowconfigure(0, weight=1); root.grid_columnconfigure(0, weight=1)
style = ttk.Style()
try: style.theme_use("vista")
except Exception: pass
root.option_add("*Font", ("Segoe UI", 9))
root.configure(bg=BG_MAIN)

# Menu
menubar = tk.Menu(root)
settings_menu = tk.Menu(menubar, tearoff=0)

# ───────── check aggiornamenti ─────────
def _parse_version(v: str):
    v = (v or "").strip().lstrip("vV")
    parts = []
    for p in v.split("."):
        try: parts.append(int("".join(ch for ch in p if ch.isdigit())))
        except Exception: parts.append(0)
    while len(parts) < 3: parts.append(0)
    return tuple(parts[:3])

def _http_get(url: str, timeout=10, accept="*/*"):
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent":"HubVNC-Updater","Accept":accept})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read()

def check_and_fetch_raw_script():
    data = _http_get(RAW_SCRIPT_URL, accept="text/plain")
    if len(data) < 10_000:
        raise RuntimeError("Script remoto troppo corto, abort.")
    text = data.decode("utf-8", "ignore")
    if "if __name__ == \"__main__\":" not in text:
        raise RuntimeError("Script remoto non valido (main mancante).")
    return text

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
        exe_url = None
        for a in assets:
            url = a.get("browser_download_url") or ""
            name = (a.get("name") or "").lower()
            if name.endswith(".exe") or name.endswith(".msi"):
                exe_url = url; break
        return tag, exe_url
    except Exception:
        return None, None

def _self_update_script(new_text: str):
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
    prog = tk.Toplevel(root); prog.title("Verifica aggiornamenti…"); prog.resizable(False, False)
    frm = ttk.Frame(prog, padding=12); frm.pack(fill="both", expand=True)
    ttk.Label(frm, text=f"Versione attuale: {VERSION}\nControllo su GitHub…").pack(pady=(2,8))
    bar = ttk.Progressbar(frm, mode="indeterminate", length=280); bar.pack(); bar.start(10)
    ttk.Button(frm, text="Chiudi", command=prog.destroy).pack(pady=(8,0))
    prog.transient(root); prog.grab_set()
    try: prog.attributes("-topmost", True)
    except Exception: pass

    def done(ok=True, msg=""):
        try: bar.stop(); prog.destroy()
        except Exception: pass
        (messagebox.showinfo if ok else messagebox.showerror)("Aggiornamenti", msg)

    def worker():
        try:
            latest = _get_latest_version_from_version_file()
            exe_url = None
            if not latest:
                latest, exe_url = _get_latest_release_via_api()
            if not latest:
                root.after(0, lambda: done(False, "Impossibile recuperare la versione più recente."))
                return
            if _parse_version(latest) <= _parse_version(VERSION):
                root.after(0, lambda: done(True, f"Hai già l'ultima versione ({VERSION}).")); return
            if getattr(sys, "frozen", False):
                def ask():
                    if exe_url:
                        if messagebox.askyesno("Nuova versione", f"Trovata {latest}.\nAprire installer?"):
                            webbrowser.open(exe_url)
                    else:
                        if messagebox.askyesno("Nuova versione", f"Trovata {latest}.\nAprire pagina delle release?"):
                            webbrowser.open(REPO_RELEASES_PAGE)
                root.after(0, ask)
            else:
                def ask_update():
                    if messagebox.askyesno("Nuova versione", f"Trovata {latest}.\nAggiornare automaticamente questo script?"):
                        try:
                            text = check_and_fetch_raw_script()
                            ok, info = _self_update_script(text)
                            if ok:
                                messagebox.showinfo("Aggiornato", f"Aggiornato a {latest}.\nBackup: {info}\nRiavvia l'app.")
                            else:
                                messagebox.showerror("Errore", f"Impossibile aggiornare:\n{info}")
                        except Exception as e:
                            messagebox.showerror("Errore", f"Download/validazione fallita:\n{e}")
                root.after(0, ask_update)
        except Exception as e:
            root.after(0, lambda: done(False, f"Errore:\n{e}"))
    threading.Thread(target=worker, daemon=True).start()

# ───────── esporta/importa ─────────
def export_connections():
    path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON","*.json")])
    if not path: return
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(DATA, f, indent=2, ensure_ascii=False)
        messagebox.showinfo("Esportazione", "Connessioni esportate.")
        log(f"Export {path}")
    except Exception as e:
        messagebox.showerror("Errore", f"Impossibile esportare:\n{e}")

def import_connections():
    path = filedialog.askopenfilename(filetypes=[("JSON","*.json")])
    if not path: return
    try:
        with open(path, "r", encoding="utf-8") as f:
            incoming = json.load(f)
        # validazione minima
        if not isinstance(incoming, dict) or "connections" not in incoming or "folders" not in incoming:
            raise ValueError("Formato non valido.")
        # backup del file corrente
        if os.path.exists(SAVE_FILE):
            shutil.copy2(SAVE_FILE, SAVE_FILE + ".bak")
        current = get_current_folder_path()
        DATA.clear(); DATA.update(incoming)
        if "Tutte" not in DATA["folders"]:
            DATA["folders"] = ["Tutte"] + [p for p in DATA["folders"] if p != "Tutte"]
        _save_data()
        refresh_folders_tree(keep_selection=current)
        refresh_connections()
        messagebox.showinfo("Importazione", "Connessioni importate.")
        log(f"Import {path}")
    except Exception as e:
        messagebox.showerror("Errore", f"Impossibile importare:\n{e}")

def reset_connections():
    if not messagebox.askyesno("Conferma", "Ripristinare le connessioni di default?"): return
    _write_default()
    _load_data()
    refresh_folders_tree(keep_selection="Tutte")
    refresh_connections()
    messagebox.showinfo("Reimpostato", "Connessioni azzerate.")
    log("Reset connections")

# ───────── impostazioni ─────────
def open_settings_dialog():
    dlg = tk.Toplevel(root); dlg.title("Configurazione"); dlg.resizable(False, False)
    frm = ttk.Frame(dlg, padding=12); frm.pack(fill="both", expand=True)

    autostart_var = tk.BooleanVar(value=is_autostart_enabled())
    ttk.Checkbutton(frm, text="Avvia HubVNC all'accesso a Windows", variable=autostart_var).grid(row=0, column=0, sticky="w", pady=4)

    admin_enabled_var = tk.BooleanVar(value=settings.get("admin_enabled", False))
    ttk.Checkbutton(frm, text="Abilita login Admin (funzioni avanzate)", variable=admin_enabled_var).grid(row=1, column=0, sticky="w", pady=4)

    def set_admin_password():
        if not admin_enabled_var.get():
            messagebox.showwarning("Attenzione", "Abilita prima il login Admin.")
            return
        pwd = simpledialog.askstring("Password Admin", "Imposta/Modifica password Admin:", show="*")
        if pwd is None: return
        settings["admin_password"] = pwd or ""
        save_settings(settings)
        messagebox.showinfo("OK", "Password Admin aggiornata.")

    ttk.Button(frm, text="Imposta Password Admin…", command=set_admin_password).grid(row=2, column=0, sticky="w", pady=(0,8))
    ttk.Button(frm, text="Verifica aggiornamenti…", command=check_for_updates).grid(row=3, column=0, sticky="w", pady=(0,8))

    # C) Autostart TightVNC su avvio HubVNC
    vnc_autostart_var = tk.BooleanVar(value=settings.get("autostart_vnc_on_launch", False))
    ttk.Checkbutton(frm, text="Avvia TightVNC Server all'apertura di HubVNC",
                    variable=vnc_autostart_var).grid(row=4, column=0, sticky="w", pady=4)

    btns = ttk.Frame(frm); btns.grid(row=10, column=0, sticky="e", pady=(10,0))
    def do_ok():
        want = autostart_var.get()
        ok = True
        if want and not is_autostart_enabled(): ok = enable_autostart()
        elif (not want) and is_autostart_enabled(): ok = disable_autostart()
        if ok:
            settings["autostart_app"] = want
            settings["admin_enabled"] = admin_enabled_var.get()
            settings["autostart_vnc_on_launch"] = vnc_autostart_var.get()
            save_settings(settings)
            dlg.destroy()
    ttk.Button(btns, text="OK", command=do_ok).pack(side="right", padx=(6,0))
    ttk.Button(btns, text="Annulla", command=dlg.destroy).pack(side="right")

def admin_login():
    # B) niente auto-abilitazione
    global is_admin
    if not settings.get("admin_enabled", False):
        messagebox.showwarning("Admin", "Login Admin non abilitato. Vai su Impostazioni per abilitarlo.")
        return
    pwd = simpledialog.askstring("Login Admin", "Password Admin:", show="*")
    if pwd is None: return
    if pwd == settings.get("admin_password", "") and pwd != "":
        is_admin = True
        update_admin_visibility()
        messagebox.showinfo("Admin", "Accesso Admin eseguito.")
        log("Admin login")
    else:
        messagebox.showerror("Admin", "Password errata.")

def admin_logout():
    global is_admin
    was = is_admin; is_admin = False; update_admin_visibility()
    if was: messagebox.showinfo("Admin", "Uscito dalla modalità Admin."); log("Admin logout")

settings_menu.add_command(label="Configurazione…", command=open_settings_dialog)
settings_menu.add_command(label="Verifica aggiornamenti…", command=check_for_updates)
settings_menu.add_separator()
settings_menu.add_command(label="Importa…", command=import_connections)
settings_menu.add_command(label="Esporta…", command=export_connections)
settings_menu.add_separator()
settings_menu.add_command(label="Reimposta connessioni", command=reset_connections)
settings_menu.add_separator()
settings_menu.add_command(label="Accedi come Admin…", command=admin_login)
settings_menu.add_command(label="Esci Admin", command=admin_logout)
settings_menu.add_separator()
settings_menu.add_command(label="Esci", command=root.destroy)
menubar.add_cascade(label="Impostazioni", menu=settings_menu)
root.config(menu=menubar)

# ───────── layout ─────────
paned = ttk.Panedwindow(root, orient="horizontal"); paned.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
left = tk.Frame(paned, bg=BG_SIDEBAR); right = tk.Frame(paned, bg=BG_MAIN)
paned.add(left, weight=1); paned.add(right, weight=4)

# Sidebar sinistra
sb_head = tk.Frame(left, bg=BG_SIDEBAR); sb_head.pack(fill="x", padx=10, pady=(10,6))
tk.Label(sb_head, text="Hub VNC/RDP/SSH", font=("Segoe UI", 10, "bold"), bg=BG_SIDEBAR).pack(anchor="w")
sb_actions = tk.Frame(left, bg=BG_SIDEBAR); sb_actions.pack(fill="x", padx=10, pady=(4,10))

def _sbbtn(parent, text, cmd):
    b = ttk.Button(parent, text=text, command=cmd); b.pack(fill="x", pady=3); return b

_sbbtn(sb_actions, "📁 Nuova cartella",           lambda: add_folder_popup())
_sbbtn(sb_actions, "📂 Nuova sottocartella",      lambda: add_subfolder_popup())
_sbbtn(sb_actions, "✎ Rinomina cartella",         lambda: rename_folder_popup())
_sbbtn(sb_actions, "🗑 Elimina cartella",          lambda: remove_folder())
_sbbtn(sb_actions, "⬆ Sposta su",                 lambda: move_folder_up())
_sbbtn(sb_actions, "⬇ Sposta giù",                lambda: move_folder_down())
_sbbtn(sb_actions, "↔ Ordina A→Z (pari livello)", lambda: sort_siblings_az())

ttk.Separator(left, orient="horizontal").pack(fill="x", padx=10, pady=(0,8))

frame_tree = tk.Frame(left, bg=BG_SIDEBAR); frame_tree.pack(expand=True, fill="both", padx=10, pady=(0,10))
sec_scroll = ttk.Scrollbar(frame_tree, orient="vertical")
folder_tree = ttk.Treeview(frame_tree, show="tree", yscrollcommand=sec_scroll.set, selectmode="browse")
sec_scroll.config(command=folder_tree.yview); sec_scroll.pack(side="right", fill="y"); folder_tree.pack(side="left", expand=True, fill="both")

# ───────── gestione cartelle (Tree) ─────────
def _split_path(path: str):
    return [p for p in (path or "").split("/") if p]

def _ensure_path_exists(path: str):
    if path == "Tutte" or not path: return
    parts = _split_path(path)
    cur = ""
    for seg in parts:
        cur = seg if not cur else f"{cur}/{seg}"
        if cur not in DATA["folders"]:
            DATA["folders"].append(cur)

def get_current_folder_path():
    sel = folder_tree.focus()
    if not sel: return "Tutte"
    return folder_tree.item(sel)["text"]

def refresh_folders_tree(keep_selection=None):
    current = keep_selection if keep_selection else get_current_folder_path()
    folder_tree.delete(*folder_tree.get_children())
    ids = {}
    root_id = folder_tree.insert("", "end", text="Tutte"); ids["Tutte"] = root_id

    for path in sorted([p for p in DATA["folders"] if p != "Tutte"], key=lambda s:(len(_split_path(s)), s.lower())):
        parts = _split_path(path)
        parent_path = "Tutte" if len(parts)==1 else "/".join(parts[:-1])
        parent_id = ids.get(parent_path)
        if not parent_id:
            _ensure_path_exists(parent_path)
            refresh_folders_tree(keep_selection=current)
            return
        iid = folder_tree.insert(parent_id, "end", text=path)
        ids[path] = iid

    def _expand_to(path):
        if path == "Tutte": return
        parts = _split_path(path)
        cur = "Tutte"
        for seg in parts:
            cur = seg if cur=="Tutte" else f"{cur}/{seg}"
            iid = ids.get(cur)
            if iid: folder_tree.item(iid, open=True)
    _expand_to(current)

    target = ids.get(current, root_id)
    try:
        folder_tree.selection_set(target); folder_tree.focus(target); folder_tree.see(target)
    except Exception:
        pass

refresh_folders_tree(keep_selection="Tutte")

def add_folder_popup():
    base = get_current_folder_path()
    def add():
        name = entry.get().strip()
        if not name: return
        if base == "Tutte":
            path = name
        else:
            parts = _split_path(base); parent = "/".join(parts[:-1])
            path = f"{parent}/{name}" if parent else name
        if path in DATA["folders"]:
            messagebox.showwarning("Attenzione", "Cartella già esistente."); return
        _ensure_path_exists(path); _save_data()
        refresh_folders_tree(keep_selection=path)
        popup.destroy(); log(f"Folder created: {path}")
    popup = tk.Toplevel(root); popup.title("Nuova Cartella")
    tk.Label(popup, text="Nome cartella:").pack(padx=10, pady=5)
    entry = tk.Entry(popup); entry.pack(padx=10, pady=5)
    tk.Button(popup, text="Aggiungi", command=add).pack(padx=10, pady=10); entry.focus()

def add_subfolder_popup():
    parent = get_current_folder_path()
    if parent == "Tutte":
        messagebox.showwarning("Attenzione", "Seleziona una cartella (non 'Tutte') per creare una sottocartella.")
        return
    def add():
        name = entry.get().strip()
        if not name: return
        path = f"{parent}/{name}"
        if path in DATA["folders"]:
            messagebox.showwarning("Attenzione", "Sottocartella già esistente."); return
        _ensure_path_exists(path); _save_data()
        refresh_folders_tree(keep_selection=path)
        popup.destroy(); log(f"Subfolder created: {path}")
    popup = tk.Toplevel(root); popup.title("Nuova Sottocartella")
    tk.Label(popup, text=f"Genitore: {parent}\nNome sottocartella:").pack(padx=10, pady=5)
    entry = tk.Entry(popup); entry.pack(padx=10, pady=5)
    tk.Button(popup, text="Aggiungi", command=add).pack(padx=10, pady=10); entry.focus()

def rename_folder_popup():
    cur = get_current_folder_path()
    if cur == "Tutte":
        messagebox.showwarning("Attenzione", "Non puoi rinominare 'Tutte'."); return
    def do_rename():
        new_name = entry.get().strip()
        if not new_name: return
        parts = _split_path(cur); parent = "/".join(parts[:-1])
        new_path = f"{parent}/{new_name}" if parent else new_name
        if new_path in DATA["folders"]:
            messagebox.showwarning("Attenzione", "Nome già esistente."); return
        updated = []
        for p in list(DATA["folders"]):
            if p == cur or p.startswith(cur + "/"):
                np = p.replace(cur, new_path, 1)
                updated.append(np)
                DATA["folders"].remove(p)
        for p in updated:
            if p not in DATA["folders"]: DATA["folders"].append(p)
        for c in DATA["connections"]:
            f = c.get("folder","")
            if f == cur or f.startswith(cur + "/"):
                c["folder"] = f.replace(cur, new_path, 1)
        _save_data()
        refresh_folders_tree(keep_selection=new_path)
        refresh_connections()
        popup.destroy(); log(f"Folder renamed: {cur} -> {new_path}")
    popup = tk.Toplevel(root); popup.title("Rinomina Cartella")
    tk.Label(popup, text=f"Nuovo nome per '{cur.split('/')[-1]}':").pack(padx=10, pady=5)
    entry = tk.Entry(popup); entry.insert(0, cur.split("/")[-1]); entry.pack(padx=10, pady=5)
    tk.Button(popup, text="Rinomina", command=do_rename).pack(padx=10, pady=10); entry.focus()

def remove_folder():
    cur = get_current_folder_path()
    if cur == "Tutte":
        messagebox.showerror("Errore", "Non puoi eliminare 'Tutte'."); return
    if not messagebox.askyesno("Conferma", f"Eliminare la cartella '{cur}' e le connessioni al suo interno?"): return
    to_remove = [p for p in DATA["folders"] if p == cur or p.startswith(cur + "/")]
    for p in to_remove:
        try: DATA["folders"].remove(p)
        except ValueError: pass
    DATA["connections"] = [c for c in DATA["connections"] if not (c.get("folder","")==cur or c.get("folder","").startswith(cur + "/"))]
    _save_data()
    refresh_folders_tree(keep_selection="Tutte"); refresh_connections(); log(f"Folder removed: {cur}")

def _siblings(path):
    if path == "Tutte": return []
    parts = _split_path(path); parent = "/".join(parts[:-1])
    return sorted([p for p in DATA["folders"] if ("/".join(_split_path(p)[:-1])==parent and p!="Tutte")], key=str.lower)

def move_folder_up():
    cur = get_current_folder_path()
    if cur == "Tutte": return
    sibs = _siblings(cur); idx = sibs.index(cur) if cur in sibs else -1
    if idx > 0:
        sibs[idx-1], sibs[idx] = sibs[idx], sibs[idx-1]
        parent = "/".join(_split_path(cur)[:-1])
        others = [p for p in DATA["folders"] if "/".join(_split_path(p)[:-1])!=parent or p=="Tutte"]
        DATA["folders"] = ["Tutte"] + others + sibs
        _save_data(); refresh_folders_tree(keep_selection=cur); log(f"Folder moved up: {cur}")

def move_folder_down():
    cur = get_current_folder_path()
    if cur == "Tutte": return
    sibs = _siblings(cur); idx = sibs.index(cur) if cur in sibs else -1
    if 0 <= idx < len(sibs)-1:
        sibs[idx+1], sibs[idx] = sibs[idx], sibs[idx+1]
        parent = "/".join(_split_path(cur)[:-1])
        others = [p for p in DATA["folders"] if "/".join(_split_path(p)[:-1])!=parent or p=="Tutte"]
        DATA["folders"] = ["Tutte"] + others + sibs
        _save_data(); refresh_folders_tree(keep_selection=cur); log(f"Folder moved down: {cur}")

def sort_siblings_az():
    cur = get_current_folder_path()
    if cur == "Tutte": return
    parent = "/".join(_split_path(cur)[:-1])
    sibs = sorted([p for p in DATA["folders"] if "/".join(_split_path(p)[:-1])==parent and p!="Tutte"], key=lambda s:s.lower())
    others = [p for p in DATA["folders"] if "/".join(_split_path(p)[:-1])!=parent or p=="Tutte"]
    DATA["folders"] = ["Tutte"] + others + sibs
    _save_data(); refresh_folders_tree(keep_selection=cur); log("Folders sorted A→Z at same level")

def on_folder_select(event):
    refresh_connections()
folder_tree.bind("<<TreeviewSelect>>", on_folder_select)

# ───────── pannello destro ─────────
content = tk.Frame(right, bg=BG_MAIN); content.pack(expand=True, fill="both", padx=10, pady=10)
toolbar = tk.Frame(content, bg=BG_MAIN); toolbar.pack(fill="x", pady=(0,8))
filter_var = tk.StringVar(); view_mode = tk.StringVar(value="icone")

ttk.Button(toolbar, text="➕ Nuova",   command=lambda: add_connection_popup(False)).pack(side="left", padx=(0,6))
ttk.Button(toolbar, text="✏️ Modifica", command=lambda: add_connection_popup(True)).pack(side="left", padx=(0,6))
ttk.Button(toolbar, text="🗑 Rimuovi",  command=lambda: remove_connection()).pack(side="left", padx=(0,6))
ttk.Button(toolbar, text="Connetti",   command=lambda: connect_to_selected()).pack(side="left", padx=(0,6))

ttk.Button(toolbar, text="⚡ VNC rapido", command=lambda: quick_vnc_connect_dialog()).pack(side="left", padx=(10,6))
ttk.Button(toolbar, text="⚡ RDP rapido", command=lambda: quick_rdp_connect_dialog()).pack(side="left", padx=(0,6))
ttk.Button(toolbar, text="⚡ SSH rapido", command=lambda: quick_ssh_connect_dialog()).pack(side="left", padx=(0,12))

ttk.Button(toolbar, text="▶ Avvia Server VNC", command=lambda: start_vnc_server()).pack(side="left", padx=(0,12))
btn_reset_vnc = ttk.Button(toolbar, text="🔑 Reset Psw VNC Remoto", command=lambda: reset_vnc_password_remote())

tk.Frame(toolbar, bg=BG_MAIN).pack(side="left", expand=True, fill="x")
ttk.Label(toolbar, text="Cerca:").pack(side="left", padx=(0,4))
ttk.Entry(toolbar, textvariable=filter_var, width=28).pack(side="left", padx=(0,12))
ttk.Button(toolbar, text="Vista: Lista",  command=lambda: set_view_mode("lista")).pack(side="left", padx=(0,6))
ttk.Button(toolbar, text="Vista: Icone",  command=lambda: set_view_mode("icone")).pack(side="left")

def update_admin_visibility():
    try:
        if is_admin:
            btn_reset_vnc.pack(side="left", padx=(10,12))
        else:
            btn_reset_vnc.pack_forget()
    except Exception:
        pass

center = tk.Frame(content, bg=BG_MAIN); center.pack(expand=True, fill="both")

# Lista (SOLO colonna Ping)
cols = ("Ping", "Nome", "Host", "Porta", "Protocollo", "Folder")
conn_list = ttk.Treeview(center, columns=cols, show="headings", selectmode="browse")
for col in cols:
    conn_list.heading(col, text=col)
    if col == "Ping": conn_list.column(col, width=60, anchor="center")
    elif col=="Porta": conn_list.column(col, width=80, anchor="w")
    else: conn_list.column(col, width=160, anchor="w")
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

# Icona
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

selected_tile = None
selected_conn = None

def set_tile_bg(tile, color):
    if not tile or not tile.winfo_exists(): return
    try:
        tile.configure(bg=color)
        for ch in tile.winfo_children():
            try: ch.configure(bg=color)
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

# ───────── Ping (cache + async) ─────────
PING_TTL = 30
_ping_cache = {}   # host -> (status_bool|None, ts)

# Thread pool for ping tasks to avoid creating many threads
_executor = ThreadPoolExecutor(max_workers=8)
# Debounce handle for refresh operations
_refresh_after_id = None

def _ping_host_once(host: str, timeout_ms: int = 600) -> bool | None:
    if not host: return None
    try:
        r = run_silent(["ping","-n","1","-w", str(timeout_ms), host],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return (r.returncode == 0)
    except Exception:
        return None

def _ping_async(host: str, on_done):
    # Submit ping work to the shared thread pool executor. on_done will be
    # called in the main thread via root.after to safely update the UI.
    def _worker_inner(h, callback):
        st = _ping_host_once(h)
        _ping_cache[h] = (st, time.time())
        try:
            root.after(0, lambda: callback(h, st))
        except Exception:
            pass
    try:
        _executor.submit(_worker_inner, host, on_done)
    except Exception:
        # Fallback to thread in case executor is not available
        threading.Thread(target=lambda: (root.after(0, lambda: on_done(host, _ping_host_once(host)))), daemon=True).start()

def _need_ping(host: str) -> bool:
    st = _ping_cache.get(host)
    return (not st) or (time.time() - st[1]) > PING_TTL


def schedule_refresh_connections(delay_ms: int = 120):
    """Debounce multiple refresh requests into a single call for UI smoothness."""
    global _refresh_after_id
    try:
        if _refresh_after_id is not None:
            root.after_cancel(_refresh_after_id)
        _refresh_after_id = root.after(delay_ms, lambda: (_clear_refresh_id(), refresh_connections()))
    except Exception:
        try:
            refresh_connections()
        except Exception:
            pass

def _clear_refresh_id():
    global _refresh_after_id
    _refresh_after_id = None

# ───────── filtro + viste ─────────
def _match_query(conn, q):
    if not q: return True
    q = q.lower()
    return any(str(conn.get(k,"")).lower().find(q) >= 0 for k in ("name","host","protocol","folder"))

def _connections_in_current_folder():
    f = get_current_folder_path()
    items = DATA["connections"]
    # Be defensive: ensure connections are dicts (old/corrupted files might include lists)
    dict_items = [c for c in items if isinstance(c, dict)]
    if f == "Tutte":
        return list(dict_items)
    pref = f + "/"
    return [c for c in dict_items if (c.get("folder","")==f or c.get("folder","").startswith(pref))]

def _get_items_filtered():
    q = filter_var.get().strip().lower()
    return [c for c in _connections_in_current_folder() if _match_query(c, q)]

def set_view_mode(mode):
    view_mode.set(mode)
    for w in center.winfo_children(): w.pack_forget()
    if mode == "lista":
        conn_list.pack(expand=True, fill="both"); refresh_connections()
    else:
        icon_canvas.pack(side="left", expand=True, fill="both"); icon_scroll.pack(side="right", fill="y"); render_icons()

# ───────── render lista ─────────
def refresh_connections():
    if view_mode.get() == "lista":
        conn_list.delete(*conn_list.get_children())
        for conn in _get_items_filtered():
            host = conn.get("host","")
            port = conn.get("port","")
            proto = conn.get("protocol","")
            folder = conn.get("folder","")
            ping_status = _ping_cache.get(host, (None, 0))[0]
            ping_bullet = "●"
            tag = "up" if ping_status is True else ("down" if ping_status is False else "unknown")

            row = conn_list.insert("", "end",
                values=(ping_bullet, conn.get("name",""), host, port, proto, folder),
                tags=(tag,))
            if _need_ping(host):
                # Debounce refreshes to avoid UI thrashing when many pings complete
                _ping_async(host, on_done=lambda h, st, r=row: schedule_refresh_connections())
    else:
        render_icons()

# ───────── render icone ─────────
def render_icons():
    for w in icon_holder.winfo_children(): w.destroy()
    items = _get_items_filtered()
    cols, pad = 4, 6
    for idx, conn in enumerate(items):
        r, c = idx // cols, idx % cols
        tile = tk.Frame(icon_holder, bd=1, relief="ridge", bg=TILE_BG)
        tile.grid(row=r, column=c, padx=pad, pady=pad, sticky="nsew"); tile._selected=False

        header = tk.Frame(tile, bg=TILE_BG); header.pack(fill="x", padx=6, pady=(6,2))
        # SOLO LED PING
        led_ping = tk.Label(header, text="●", font=("Segoe UI", 9, "bold"), bg=TILE_BG, fg="#9ca3af")
        led_ping.pack(side="left", padx=(0,6))
        tk.Label(header, text=conn.get("name",""), font=("Segoe UI", 10, "bold"), bg=TILE_BG).pack(side="left", anchor="w")

        if conn_icon_small: tk.Label(tile, image=conn_icon_small, bg=TILE_BG).pack(pady=(0,2))
        else: make_monitor_icon(tile, 0.6).pack(pady=(0,2))

        tk.Label(tile, text=f"{conn.get('host','')}:{conn.get('port','')}", fg="#555", bg=TILE_BG).pack()
        tk.Label(tile, text=f"{conn.get('protocol','')} • {conn.get('folder','')}", fg="#777", bg=TILE_BG).pack()

        btns = ttk.Frame(tile); btns.pack(pady=(4,8))
        ttk.Button(btns, text="Apri", width=8, command=lambda c=conn: connect_connection_dict(c)).pack(side="left")
        ttk.Button(btns, text="✏", width=3, command=lambda c=conn, t=tile: (select_tile(t, c), add_connection_popup(True))).pack(side="left", padx=3)
        ttk.Button(btns, text="🗑", width=3, command=lambda c=conn: remove_connection_from_dict(c)).pack(side="left")

        def on_enter(e, t=tile): set_tile_bg(t, TILE_SELECTED if getattr(t,"_selected",False) else TILE_HOVER)
        def on_leave(e, t=tile): set_tile_bg(t, TILE_SELECTED if getattr(t,"_selected",False) else TILE_BG)
        bind_recursive(tile, "<Enter>", on_enter); bind_recursive(tile, "<Leave>", on_leave)
        tile.bind("<Button-1>", lambda e, t=tile, cd=conn: select_tile(t, cd))
        tile.bind("<Double-Button-1>", lambda e, cd=conn: connect_connection_dict(cd))

        host = conn.get("host","")
        def paint():
            if not led_ping.winfo_exists(): return
            st = _ping_cache.get(host, (None,0))[0]
            if st is True:  led_ping.configure(text="●", fg="#22c55e")
            elif st is False: led_ping.configure(text="●", fg="#ef4444")
            else: led_ping.configure(text="●", fg="#9ca3af")

        paint()
        if _need_ping(host):
            # For icon tiles, update the LED directly on ping completion
            _ping_async(host, on_done=lambda h, st: paint())

        # Enable dragging of tiles to folders: bind to tile and all children
        def _icon_start(ev, t=tile, cd=conn):
            select_tile(t, cd)
            drag_data['conn'] = cd

        bind_recursive(tile, '<Button-1>', _icon_start)
        bind_recursive(tile, '<B1-Motion>', _on_drag_motion)
        bind_recursive(tile, '<ButtonRelease-1>', lambda e: perform_drop_on_tree(e))

    for i in range(cols): icon_holder.grid_columnconfigure(i, weight=1)

# ───────── CRUD connessioni ─────────
def add_connection_popup(edit=False):
    current_folder = get_current_folder_path()
    if current_folder=="Tutte" and not edit:
        return messagebox.showwarning("Attenzione","Seleziona una cartella specifica prima di aggiungere.")

    existing = None
    if edit:
        sel = conn_list.focus()
        if sel:
            vals = conn_list.item(sel)["values"]
            # Ping, Nome, Host, Porta, Protocollo, Folder
            name, host, port, proto, folder = vals[1], vals[2], vals[3], vals[4], vals[5]
        elif selected_conn:
            name, host, port, proto, folder = selected_conn.get("name",""), selected_conn.get("host",""), selected_conn.get("port",""), selected_conn.get("protocol",""), selected_conn.get("folder","")
        else:
            return messagebox.showwarning("Attenzione","Seleziona una connessione da modificare.")
        for c in DATA["connections"]:
            if c.get("name")==name and c.get("host")==host and str(c.get("port"))==str(port) and c.get("protocol")==proto and c.get("folder")==folder:
                existing = c; break

    def on_proto_change(_=None):
        p = combo_proto.get()
        entry_port.configure(state="normal")
        if p == "VNC":
            pw_row.grid()
            rdp_row.grid_remove()
            if not edit and not entry_password.get():
                entry_password.insert(0, "500rossa")
            if not entry_port.get().strip():
                entry_port.insert(0, "5900")
        elif p == "RDP":
            pw_row.grid_remove()
            rdp_row.grid(row=6, column=0, columnspan=2, sticky="w")
            entry_port.delete(0,"end"); entry_port.insert(0,"3389")
        else:
            pw_row.grid_remove(); rdp_row.grid_remove()
            entry_port.delete(0,"end"); entry_port.insert(0,"22")

    popup = tk.Toplevel(root); popup.title("Modifica Connessione" if edit else "Nuova Connessione")
    frm = ttk.Frame(popup, padding=10); frm.pack(fill="both", expand=True)

    ttk.Label(frm, text="Nome:").grid(row=0,column=0,sticky="w",pady=3); entry_name=ttk.Entry(frm,width=32); entry_name.grid(row=0,column=1,sticky="ew",pady=3)
    ttk.Label(frm, text="Host/IP:").grid(row=1,column=0,sticky="w",pady=3); entry_host=ttk.Entry(frm,width=32); entry_host.grid(row=1,column=1,sticky="ew",pady=3)
    ttk.Label(frm, text="Porta:").grid(row=2,column=0,sticky="w",pady=3); entry_port=ttk.Entry(frm,width=10); entry_port.grid(row=2,column=1,sticky="w",pady=3)
    ttk.Label(frm, text="Protocollo:").grid(row=3,column=0,sticky="w",pady=3)
    combo_proto=ttk.Combobox(frm, values=["VNC","RDP","SSH"], state="readonly", width=8); combo_proto.set("VNC"); combo_proto.grid(row=3,column=1,sticky="w",pady=3)

    ttk.Label(frm, text="Cartella:").grid(row=4,column=0,sticky="w",pady=3)
    folder_values = [p for p in DATA["folders"] if p!="Tutte"]
    combo_folder = ttk.Combobox(frm, values=folder_values, state="readonly", width=30)
    combo_folder.grid(row=4, column=1, sticky="w", pady=3)
    if current_folder!="Tutte" and current_folder in folder_values:
        combo_folder.set(current_folder)
    elif folder_values:
        combo_folder.set(folder_values[0])

    pw_row = ttk.Frame(frm)
    ttk.Label(pw_row, text="Password VNC (max 8):").grid(row=0, column=0, sticky="w", pady=3)
    entry_password = ttk.Entry(pw_row, width=20, show="*"); entry_password.grid(row=0, column=1, sticky="w", pady=3)
    pw_row.grid(row=5, column=0, columnspan=2, sticky="w")

    rdp_row = ttk.Frame(frm)
    ttk.Label(rdp_row, text="Nome utente (dominio\\utente):").grid(row=0, column=0, sticky="w", pady=3)
    entry_rdp_user = ttk.Entry(rdp_row, width=28); entry_rdp_user.grid(row=0, column=1, sticky="w", pady=3)

    frm.grid_columnconfigure(1,weight=1); combo_proto.bind("<<ComboboxSelected>>", on_proto_change)

    def save_conn():
        name=entry_name.get().strip()
        host=entry_host.get().strip()
        proto=combo_proto.get().strip().upper()
        port_input=entry_port.get().strip()
        vnc_password = entry_password.get().strip() if proto=="VNC" else ""
        rdp_user = entry_rdp_user.get().strip() if proto=="RDP" else ""
        folder = combo_folder.get().strip()

        try:
            port_int = int(port_input) if port_input else (5900 if proto=="VNC" else (3389 if proto=="RDP" else 22))
        except:
            return messagebox.showerror("Errore","Porta deve essere un numero")

        if not folder:
            return messagebox.showerror("Errore","Seleziona una cartella valida.")
        _ensure_path_exists(folder)

        if name and host and proto in ["VNC","RDP","SSH"]:
            conn_payload = {"name":name,"host":host,"port":port_int,"protocol":proto,"folder":folder}
            if proto == "VNC":
                if not edit and not vnc_password: vnc_password = "500rossa"
                conn_payload["password"] = vnc_password
            elif proto == "RDP":
                if rdp_user: conn_payload["username"] = rdp_user

            if edit and existing:
                if existing.get("password") and proto!="VNC": existing.pop("password", None)
                if existing.get("username") and proto!="RDP": existing.pop("username", None)
                existing.update(conn_payload)
                log(f"Edited connection: {name} ({host}:{port_int} {proto})")
            else:
                DATA["connections"].append(conn_payload)
                log(f"Created connection: {name} in '{folder}' ({host}:{port_int} {proto})")
            _save_data()
            refresh_folders_tree(keep_selection=folder)
            refresh_connections()
            popup.destroy()
        else:
            messagebox.showerror("Errore","Compila tutti i campi correttamente")

    btns=ttk.Frame(frm); btns.grid(row=7,column=0,columnspan=2,pady=(8,0))
    ttk.Button(btns,text="Salva",command=save_conn).pack(side="left",padx=4)
    ttk.Button(btns,text="Annulla",command=popup.destroy).pack(side="left")

    if edit and existing:
        entry_name.insert(0, existing.get("name",""))
        entry_host.insert(0, existing.get("host",""))
        entry_port.insert(0, existing.get("port",""))
        combo_proto.set(existing.get("protocol","VNC"))
        if existing.get("protocol","").upper()=="VNC":
            pw_row.grid(); entry_password.insert(0, existing.get("password",""))
        elif existing.get("protocol","").upper()=="RDP":
            rdp_row.grid(row=6, column=0, columnspan=2, sticky="w"); entry_rdp_user.insert(0, existing.get("username",""))
        combo_folder.set(existing.get("folder","") or (current_folder if current_folder!="Tutte" else folder_values[0] if folder_values else ""))
    else:
        entry_port.insert(0, "5900")

    on_proto_change()
    entry_name.focus()

def remove_connection_from_dict(conn):
    if not messagebox.askyesno("Conferma", f"Eliminare '{conn.get('name')}'?"): return
    try:
        DATA["connections"].remove(conn)
        _save_data(); refresh_connections(); log(f"Removed connection: {conn.get('name')}")
    except ValueError:
        pass

def remove_connection():
    sel = conn_list.focus()
    if not sel:
        if selected_conn: remove_connection_from_dict(selected_conn)
        else: messagebox.showwarning("Attenzione","Seleziona una connessione.")
        return
    vals = conn_list.item(sel)["values"]
    # Ping, Nome, Host, Porta, Protocollo, Folder
    name, host, port, proto, folder = vals[1], vals[2], vals[3], vals[4], vals[5]
    for c in DATA["connections"]:
        if c.get("name")==name and c.get("host")==host and str(c.get("port"))==str(port) and c.get("protocol")==proto and c.get("folder")==folder:
            remove_connection_from_dict(c); return

# ───────── Connetti ─────────
def _launch_rdp_with_rdpfile(host: str, port: int | str, username: str | None):
    try: port = int(port)
    except Exception: port = 3389
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
    if username: lines.append(f"username:s:{username}")
    rdp_path = os.path.join(APPDATA_DIR, "last.rdp")
    try:
        with open(rdp_path, "w", encoding="utf-8") as f: f.write("\n".join(lines))
        subprocess.Popen(["mstsc", rdp_path])
        log(f"Opened RDP via file: {host}:{port} (user={username or 'N/D'})")
    except Exception as e:
        messagebox.showerror("Errore RDP", f"Impossibile avviare mstsc:\n{e}")

def _mark_last_vnc(host: str):
    DATA.setdefault("last_vnc_time", {})
    DATA["last_vnc_time"][host] = datetime.now().isoformat(timespec="seconds")
    _save_data()

def connect_connection_dict(cdict):
    def _normalize_connection(obj):
        # Accept dicts or row-value lists/tuples from the Treeview
        if isinstance(obj, dict):
            return obj
        if isinstance(obj, (list, tuple)):
            # Common Treeview row shape: (Ping, Name, Host, Port, Protocol, Folder)
            if len(obj) >= 6:
                return {"name": obj[1], "host": obj[2], "port": obj[3], "protocol": obj[4], "folder": obj[5]}
            # Alternative: (Name, Host, Port, Protocol, Folder)
            if len(obj) >= 5:
                return {"name": obj[0], "host": obj[1], "port": obj[2], "protocol": obj[3], "folder": obj[4]}
        return None

    try:
        norm = _normalize_connection(cdict)
        if not norm:
            raise ValueError("Connessione non valida")
        host = norm.get("host", "")
        port = norm.get("port", "")
        proto = (norm.get("protocol", "") or "").upper()
        if proto == "VNC":
            if os.path.exists(VNC_VIEWER):
                args = [VNC_VIEWER]
                if norm.get("password"):
                    args.append(f"-password={norm.get('password')}")
                args.append(f"{host}::{port}")
                subprocess.Popen(args)
                _mark_last_vnc(host)
                log(f"Opened VNC to {host}:{port} (with_password={bool(norm.get('password'))})")
                schedule_refresh_connections()
            else:
                messagebox.showerror("Errore", "Viewer VNC non trovato!")
        elif proto == "RDP":
            username = (norm.get("username", "") or "").strip()
            _launch_rdp_with_rdpfile(host, port, username)
        elif proto == "SSH":
            putty = shutil.which("putty") or "putty"
            subprocess.Popen([putty, "-P", str(port), host])
            log(f"Opened SSH to {host}:{port}")
        else:
            messagebox.showerror("Errore", f"Protocollo {proto} non supportato.")
    except Exception as e:
        tb = traceback.format_exc()
        log(f"Connection error: {e!r}\n{tb}")
        messagebox.showerror("Errore", f"Impossibile avviare la connessione:\n{e}")

def connect_to_selected():
    sel = conn_list.focus()
    if sel:
        vals = conn_list.item(sel)["values"]
        # Ping, Nome, Host, Porta, Protocollo, Folder
        name, host, port, proto, folder = vals[1], vals[2], vals[3], vals[4], vals[5]
        for c in DATA["connections"]:
            if c.get("name")==name and c.get("host")==host and str(c.get("port"))==str(port) and c.get("protocol")==proto and c.get("folder")==folder:
                connect_connection_dict(c); return
        # If no match found in DATA (possible corrupted list), try connecting using values from the row
        try:
            fallback = {"name": name, "host": host, "port": int(port) if str(port).isdigit() else port, "protocol": proto, "folder": folder}
            connect_connection_dict(fallback)
            return
        except Exception:
            pass
    if selected_conn:
        connect_connection_dict(selected_conn); return
    messagebox.showwarning("Selezione mancante", "Seleziona una connessione.")

# ───────── Quick connect ─────────
def quick_vnc_connect(host: str, port: int, password: str | None):
    if not os.path.exists(VNC_VIEWER):
        messagebox.showerror("Errore", "Viewer VNC non trovato (tvnviewer.exe)."); return
    args = [VNC_VIEWER]
    if password: args.append(f"-password={password}")
    args.append(f"{host}::{port}")
    try:
        subprocess.Popen(args); _mark_last_vnc(host); log(f"Quick VNC -> {host}:{port}")
        refresh_connections()
    except Exception as e:
        messagebox.showerror("Errore", f"Impossibile aprire VNC:\n{e}")

def quick_vnc_connect_dialog():
    dlg = tk.Toplevel(root); dlg.title("VNC rapido"); dlg.resizable(False, False)
    frm = ttk.Frame(dlg, padding=12); frm.pack(fill="both", expand=True)
    host_var = tk.StringVar(); port_var = tk.StringVar(value="5900"); pw_var = tk.StringVar()
    ttk.Label(frm, text="Host/IP:").grid(row=0, column=0, sticky="w", pady=4); e_host = ttk.Entry(frm, textvariable=host_var, width=32); e_host.grid(row=0, column=1, sticky="ew", pady=4)
    ttk.Label(frm, text="Porta:").grid(row=1, column=0, sticky="w", pady=4); e_port = ttk.Entry(frm, textvariable=port_var, width=8); e_port.grid(row=1, column=1, sticky="w", pady=4)
    ttk.Label(frm, text="Password (max 8):").grid(row=2, column=0, sticky="w", pady=4); e_pw = ttk.Entry(frm, textvariable=pw_var, show="*"); e_pw.grid(row=2, column=1, sticky="ew", pady=4)
    btns = ttk.Frame(frm); btns.grid(row=3, column=0, columnspan=2, sticky="e", pady=(8,0))
    def do_open():
        h, p_int = parse_host_and_port(host_var.get(), port_var.get(), 5900)
        if not h: messagebox.showerror("Errore", "Inserisci Host/IP."); return
        pw = pw_var.get().strip()
        if len(pw) > 8: messagebox.showerror("Errore", "La password VNC deve essere max 8 caratteri."); return
        dlg.destroy(); quick_vnc_connect(h, p_int, pw or None)
    ttk.Button(btns, text="Annulla", command=dlg.destroy).pack(side="right", padx=(6,0))
    ttk.Button(btns, text="Apri", command=do_open).pack(side="right")
    frm.grid_columnconfigure(1, weight=1); dlg.transient(root); dlg.grab_set(); e_host.focus_set(); dlg.bind("<Return>", lambda e: do_open())

def quick_rdp_connect(host: str, port: int, username: str | None):
    _launch_rdp_with_rdpfile(host, port, username)

def quick_rdp_connect_dialog():
    dlg = tk.Toplevel(root); dlg.title("RDP rapido"); dlg.resizable(False, False)
    frm = ttk.Frame(dlg, padding=12); frm.pack(fill="both", expand=True)
    host_var = tk.StringVar(); port_var = tk.StringVar(value="3389"); user_var = tk.StringVar()
    ttk.Label(frm, text="Host/IP:").grid(row=0, column=0, sticky="w", pady=4); e_host = ttk.Entry(frm, textvariable=host_var, width=32); e_host.grid(row=0, column=1, sticky="ew", pady=4)
    ttk.Label(frm, text="Porta:").grid(row=1, column=0, sticky="w", pady=4); e_port = ttk.Entry(frm, textvariable=port_var, width=8); e_port.grid(row=1, column=1, sticky="w", pady=4)
    ttk.Label(frm, text="Utente (dominio\\utente, opzionale):").grid(row=2, column=0, sticky="w", pady=4); e_user = ttk.Entry(frm, textvariable=user_var, width=28); e_user.grid(row=2, column=1, sticky="ew", pady=4)
    btns = ttk.Frame(frm); btns.grid(row=3, column=0, columnspan=2, sticky="e", pady=(8,0))
    def do_open():
        h, p_int = parse_host_and_port(host_var.get(), port_var.get(), 3389)
        if not h: messagebox.showerror("Errore", "Inserisci Host/IP."); return
        u = user_var.get().strip() or None
        dlg.destroy(); quick_rdp_connect(h, p_int, u)
    ttk.Button(btns, text="Annulla", command=dlg.destroy).pack(side="right", padx=(6,0))
    ttk.Button(btns, text="Apri", command=do_open).pack(side="right")
    frm.grid_columnconfigure(1, weight=1); dlg.transient(root); dlg.grab_set(); e_host.focus_set(); dlg.bind("<Return>", lambda e: do_open())

def quick_ssh_connect(host: str, port: int, username: str | None):
    putty = shutil.which("putty") or "putty"
    args = [putty]
    if username: args += ["-l", username]
    args += ["-P", str(port), host]
    try:
        subprocess.Popen(args); log(f"Quick SSH -> {host}:{port} (user={username or 'N/D'})")
    except Exception as e:
        messagebox.showerror("Errore", f"Impossibile avviare PuTTY:\n{e}")

def quick_ssh_connect_dialog():
    dlg = tk.Toplevel(root); dlg.title("SSH rapido"); dlg.resizable(False, False)
    frm = ttk.Frame(dlg, padding=12); frm.pack(fill="both", expand=True)
    host_var = tk.StringVar(); port_var = tk.StringVar(value="22"); user_var = tk.StringVar()
    ttk.Label(frm, text="Host/IP:").grid(row=0, column=0, sticky="w", pady=4); e_host = ttk.Entry(frm, textvariable=host_var, width=32); e_host.grid(row=0, column=1, sticky="ew", pady=4)
    ttk.Label(frm, text="Porta:").grid(row=1, column=0, sticky="w", pady=4); e_port = ttk.Entry(frm, textvariable=port_var, width=8); e_port.grid(row=1, column=1, sticky="w", pady=4)
    ttk.Label(frm, text="Utente (opzionale):").grid(row=2, column=0, sticky="w", pady=4); e_user = ttk.Entry(frm, textvariable=user_var, width=28); e_user.grid(row=2, column=1, sticky="ew", pady=4)
    btns = ttk.Frame(frm); btns.grid(row=3, column=0, columnspan=2, sticky="e", pady=(8,0))
    def do_open():
        h, p_int = parse_host_and_port(host_var.get(), port_var.get(), 22)
        if not h: messagebox.showerror("Errore", "Inserisci Host/IP."); return
        u = user_var.get().strip() or None
        dlg.destroy(); quick_ssh_connect(h, p_int, u)
    ttk.Button(btns, text="Annulla", command=dlg.destroy).pack(side="right", padx=(6,0))
    ttk.Button(btns, text="Apri", command=do_open).pack(side="right")
    frm.grid_columnconfigure(1, weight=1); dlg.transient(root); dlg.grab_set(); e_host.focus_set(); dlg.bind("<Return>", lambda e: do_open())

# ───────── VNC password encode / TightVNC locale ─────────
def vnc_encode_password(raw_password: str) -> bytes | None:
    if DES is None: return None
    key = bytes([0x23,0x52,0x6B,0x06,0x23,0x4E,0x58,0x07])
    def bitrev(b): v=b; v=((v&0xF0)>>4)|((v&0x0F)<<4); v=((v&0xCC)>>2)|((v&0x33)<<2); v=((v&0xAA)>>1)|((v&0x55)<<1); return v
    key = bytes(bitrev(b) for b in key)
    pw = (raw_password or "")[:8].encode("latin1","ignore").ljust(8, b"\x00")
    return DES.new(key, DES.MODE_ECB).encrypt(pw)

def _reg_set_hkcu_tvn_str_or_bin(name: str, value, regtype):
    if not winreg: return False
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\TightVNC\Server") as k:
            winreg.SetValueEx(k, name, 0, regtype, value)
        return True
    except Exception:
        return False

def _reg_set_hkcu_tvn_dword(name: str, d: int) -> bool:
    return _reg_set_hkcu_tvn_str_or_bin(name, int(d), winreg.REG_DWORD)

def _reg_set_hkcu_tvn_password(raw_password: str) -> bool:
    if DES is None or not winreg: return False
    enc = vnc_encode_password(raw_password)
    if not enc: return False
    return _reg_set_hkcu_tvn_str_or_bin("Password", enc, winreg.REG_BINARY)

def _reg_get_hkcu_tvn_dword(name: str, default=None):
    if not winreg: return default
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\TightVNC\Server") as k:
            val, typ = winreg.QueryValueEx(k, name)
            if typ == winreg.REG_DWORD: return int(val)
    except Exception:
        pass
    return default

# A) Firewall: evita duplicati
def open_firewall_port(port_int):
    try:
        name = f"HubVNC {port_int}"
        run_silent(["netsh","advfirewall","firewall","delete","rule",
                    f"name={name}","protocol=TCP",f"localport={port_int}"],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        run_silent(["netsh","advfirewall","firewall","add","rule",
                    f"name={name}","dir=in","action=allow","protocol=TCP",
                    f"localport={port_int}","enable=yes"],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log(f"FW rule error: {e!r}")

def start_vnc_server():
    popup = tk.Toplevel(root); popup.title("Avvia Server VNC"); popup.resizable(False, False)
    frm = ttk.Frame(popup, padding=12); frm.pack(fill="both", expand=True)
    pw_var = tk.StringVar(); pw2_var = tk.StringVar()
    existing_port = _reg_get_hkcu_tvn_dword("RfbPort", 5900); port_var = tk.StringVar(value=str(existing_port))
    fw_var = tk.BooleanVar(value=True)
    ttk.Label(frm, text="Password (max 8):").grid(row=0, column=0, sticky="w", pady=4)
    e1 = ttk.Entry(frm, textvariable=pw_var, show="*"); e1.grid(row=0, column=1, sticky="ew", pady=4)
    ttk.Label(frm, text="Conferma password:").grid(row=1, column=0, sticky="w", pady=4)
    e2 = ttk.Entry(frm, textvariable=pw2_var, show="*"); e2.grid(row=1, column=1, sticky="ew", pady=4)
    ttk.Label(frm, text="Porta TCP:").grid(row=2, column=0, sticky="w", pady=4)
    e3 = ttk.Entry(frm, textvariable=port_var, width=8); e3.grid(row=2, column=1, sticky="w", pady=4)
    ttk.Checkbutton(frm, text="Apri firewall su questa porta", variable=fw_var).grid(row=3, column=0, columnspan=2, sticky="w", pady=(2,8))
    btns = ttk.Frame(frm); btns.grid(row=4, column=0, columnspan=2, sticky="e")
    def do_start():
        pw, pw2, port_txt = pw_var.get(), pw2_var.get(), port_var.get().strip()
        if pw != pw2: messagebox.showerror("Errore", "Le password non coincidono."); return
        if len(pw) > 8: messagebox.showerror("Errore", "Password VNC max 8 caratteri."); return
        try:
            port_int = int(port_txt)
            if not (1 <= port_int <= 65535): raise ValueError()
        except Exception:
            messagebox.showerror("Errore", "Porta non valida."); return
        wrote_pw = True
        if pw:
            if DES is None:
                wrote_pw = False
            else:
                wrote_pw = _reg_set_hkcu_tvn_password(pw)
        _reg_set_hkcu_tvn_dword("UseVncAuthentication", 1)
        _reg_set_hkcu_tvn_dword("AcceptRfbConnections", 1)
        _reg_set_hkcu_tvn_dword("AlwaysShared", 1)
        _reg_set_hkcu_tvn_dword("LoopbackOnly", 0)
        _reg_set_hkcu_tvn_dword("RfbPort", port_int)
        if not os.path.exists(VNC_SERVER):
            messagebox.showerror("Errore", "tvnserver.exe non trovato nella cartella dell'app."); return
        try:
            popen_silent([VNC_SERVER, "-run"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); log("Started TightVNC Server")
        except Exception as e:
            messagebox.showerror("Errore", f"Impossibile avviare TightVNC:\n{e}"); return
        if fw_var.get(): open_firewall_port(port_int)
        # D) stato esito password più esplicito
        info_pw = "Password impostata."
        if pw and not wrote_pw:
            info_pw = "Password NON impostata (pycryptodome mancante)."
        if not pw:
            info_pw = "Avvio SENZA password: NON RACCOMANDATO."
        messagebox.showinfo("Server VNC avviato", f"{info_pw}\nPorta: {port_int}\nCollegati con IP::{port_int}.")
        popup.destroy()
    ttk.Button(btns, text="Annulla", command=popup.destroy).pack(side="right", padx=(6,0))
    ttk.Button(btns, text="Avvia", command=do_start).pack(side="right")
    frm.grid_columnconfigure(1, weight=1); e1.focus_set(); popup.transient(root); popup.grab_set()

# ───────── Reset password VNC remoto (HKLM) ─────────
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

def _run_sc(host, args, timeout=20):
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
    t0 = time.time()
    while time.time() - t0 < timeout:
        rc, out = _run_sc(host, ["query", svc], timeout=8)
        if rc == 0 and f"STATE" in out and desired in out:
            return True
        time.sleep(poll_every)
    return False

def _check_remote_registry(host):
    if not winreg:
        raise RuntimeError("API registro non disponibili su questo sistema.")
    try:
        reg = winreg.ConnectRegistry(rf"\\{host}", winreg.HKEY_LOCAL_MACHINE)
        try: reg.Close()
        except Exception: pass
        return True
    except OSError as e:
        raise

# G) Enumerazione servizio TightVNC più robusta
def _find_tightvnc_service_name(host):
    rc, out = _run_sc(host, ["query", "state=", "all"], timeout=20)
    if rc != 0: return ["tvnserver", "TightVNC Server"]
    cand = []
    for line in (out or "").splitlines():
        if "SERVICE_NAME" in line and ("tvn" in line.lower() or "tightvnc" in line.lower()):
            name = line.split(":",1)[1].strip()
            cand.append(name)
    return cand or ["tvnserver", "TightVNC Server"]

def reset_vnc_password_remote():
    if not is_admin:
        messagebox.showerror("Admin richiesto", "Funzione disponibile solo in modalità Admin.\nImpostazioni → Accedi come Admin…")
        return
    if DES is None:
        messagebox.showerror("Errore","Installa pycryptodome")
        return
    host = simpledialog.askstring("Reset Password VNC Remoto","Host remoto:")
    if not host: return
    pw = simpledialog.askstring("Reset Password VNC Remoto","Nuova password (max 8):", show="*")
    if not pw: return
    if len(pw) > 8:
        messagebox.showerror("Errore", "La password VNC deve essere max 8 caratteri."); return

    prog = tk.Toplevel(root); prog.title("Reset in corso…"); prog.resizable(False, False)
    frm = ttk.Frame(prog, padding=12); frm.pack(fill="both", expand=True)
    ttk.Label(frm, text=f"Host: {host}\nOperazione in corso…").pack(pady=6)
    bar = ttk.Progressbar(frm, mode="indeterminate", length=260); bar.pack(pady=6); bar.start(10)
    prog.transient(root); prog.grab_set()
    try: prog.attributes("-topmost", True)
    except Exception: pass

    result = {"ok": False, "msg": "", "detail": ""}

    def finish():
        try: bar.stop(); prog.destroy()
        except Exception: pass
        if result["ok"]:
            messagebox.showinfo("Reset Password", result["msg"] + ("\n\n" + result["detail"] if result["detail"] else ""))
            log(f"Remote VNC password reset on {host} (ok)")
        else:
            messagebox.showerror("Errore", result["msg"] + ("\n\nDettagli:\n" + result["detail"] if result["detail"] else ""))
            log(f"Remote VNC password reset on {host} FAILED: {result['detail']}")

    def worker():
        try:
            try:
                if _ping_host_once(host, 800) is False:
                    result["detail"] += "Avviso: host non risponde al ping.\n"
            except Exception:
                pass
            _check_remote_registry(host)
            enc = vnc_encode_password(pw)
            if not enc: raise RuntimeError("Codifica password fallita (pycryptodome).")
            reg = _connect_remote_hklm(host)
            path_used = _write_tightvnc_pw_hklm(reg, enc)
            try: reg.Close()
            except Exception: pass

            restarted = False
            for svc in _find_tightvnc_service_name(host):
                _run_sc(host, ["stop", svc], timeout=15)
                _wait_service_state(host, svc, "STOPPED", timeout=20)
                rc, _ = _run_sc(host, ["start", svc], timeout=20)
                if rc == 0 and _wait_service_state(host, svc, "RUNNING", timeout=25):
                    restarted = True; break

            result["ok"] = True
            result["msg"] = (f"Password aggiornata su {host}\n"
                             f"Chiave: HKLM\\{path_used}\n"
                             f"Servizio riavviato: {'SÌ' if restarted else 'NO'}")
        except OSError as e:
            result["msg"] = "Impossibile completare il reset."
            result["detail"] += str(e)
        except Exception as e:
            result["msg"] = "Impossibile completare il reset."
            result["detail"] += str(e)
        root.after(0, finish)
    threading.Thread(target=worker, daemon=True).start()

# ───────── DnD lista → tree (migliorato) ─────────
drag_data = {"conn": None}

def _conn_from_item_values(vals):
    # vals = (Ping, Nome, Host, Porta, Protocollo, Folder)
    if not vals or len(vals) < 6:
        return None
    name, host, port, proto, folder = vals[1], vals[2], str(vals[3]), vals[4], vals[5]
    for c in DATA.get("connections", []):
        if not isinstance(c, dict):
            continue
        try:
            if (c.get("name") == name and c.get("host") == host and
                str(c.get("port")) == port and c.get("protocol") == proto and
                c.get("folder") == folder):
                return c
        except Exception:
            continue
    return None

def _widget_is_descendant(widget, ancestor):
    w = widget
    while w is not None:
        if w == ancestor:
            return True
        w = getattr(w, "master", None)
    return False

def _list_start(e):
    """Seleziona la riga sotto il mouse quando inizio il drag."""
    row_id = conn_list.identify_row(e.y)
    if not row_id:
        drag_data["conn"] = None
        return
    conn_list.selection_set(row_id)
    vals = conn_list.item(row_id)["values"]
    drag_data["conn"] = _conn_from_item_values(vals)

def _on_drag_motion(e):
    if not drag_data.get("conn"):
        return
    x, y = root.winfo_pointerxy()
    target = root.winfo_containing(x, y)
    if target and _widget_is_descendant(target, folder_tree):
        local_y = y - folder_tree.winfo_rooty()
        row_id = folder_tree.identify_row(local_y)
        try:
            if row_id:
                folder_tree.selection_set(row_id)
                folder_tree.see(row_id)
            else:
                folder_tree.selection_remove(folder_tree.selection())
        except Exception:
            pass
    else:
        try:
            folder_tree.selection_remove(folder_tree.selection())
        except Exception:
            pass

def perform_drop_on_tree(event=None):
    if not drag_data.get("conn"):
        return
    x, y = root.winfo_pointerxy()
    target = root.winfo_containing(x, y)

    to_folder = None
    if target and _widget_is_descendant(target, folder_tree):
        local_y = y - folder_tree.winfo_rooty()
        row_id = folder_tree.identify_row(local_y)
        if row_id:
            to_folder = folder_tree.item(row_id)["text"]
            if to_folder == "Tutte": to_folder = None

    # pulizia highlight
    try:
        folder_tree.selection_remove(folder_tree.selection())
    except Exception:
        pass

    if to_folder:
        conn_obj = drag_data.get("conn")
        # Defensive: ensure conn_obj is a dict and still present in DATA
        if not isinstance(conn_obj, dict):
            log(f"Drag: unexpected conn object type: {type(conn_obj)!r} value={repr(conn_obj)[:200]}")
            drag_data["conn"] = None
            return
        # Ensure we are updating the authoritative object from DATA (match by identity or key)
        if conn_obj not in DATA.get("connections", []):
            # try to find matching connection in DATA by keys
            for c in DATA.get("connections", []):
                if (c.get("name") == conn_obj.get("name") and c.get("host") == conn_obj.get("host") and str(c.get("port")) == str(conn_obj.get("port"))):
                    conn_obj = c; break
        conn_obj["folder"] = to_folder
        _save_data(); schedule_refresh_connections(); log(f"Moved connection '{conn_obj.get('name')}' -> '{to_folder}'")

    drag_data["conn"] = None

conn_list.bind("<Button-1>", _list_start)
conn_list.bind("<B1-Motion>", _on_drag_motion)
conn_list.bind("<ButtonRelease-1>", perform_drop_on_tree)
folder_tree.bind("<ButtonRelease-1>", perform_drop_on_tree)
root.bind("<Escape>", lambda e: drag_data.__setitem__('conn', None))

# ───────── Context menu ─────────
ctx = tk.Menu(root, tearoff=0)
ctx.add_command(label="Connetti", command=lambda: connect_to_selected())
ctx.add_command(label="Modifica", command=lambda: add_connection_popup(edit=True))
ctx.add_command(label="Rimuovi",  command=lambda: remove_connection())
icon_holder.bind("<Button-3>", lambda e: (ctx.tk_popup(e.x_root, e.y_root), ctx.grab_release()))
conn_list.bind("<Button-3>",   lambda e: (ctx.tk_popup(e.x_root, e.y_root), ctx.grab_release()))

# ───────── Statusbar ─────────
hostname, user, osver, ip = get_pc_info()
status = ttk.Label(root, anchor="w", relief="groove",
                   text=f" Utente: {user}  •  PC: {hostname}  •  IP: {ip}  •  OS: {osver}")
status.grid(row=1, column=0, sticky="ew", padx=8, pady=(0,8))

# ───────── Eventi generali ─────────
root.bind("<Control-n>", lambda e: add_connection_popup(False))
root.bind("<Control-e>", lambda e: add_connection_popup(True))
root.bind("<Delete>",    lambda e: remove_connection())
root.bind("<Return>",    lambda e: connect_to_selected())

root.bind("<Control-Shift-V>", lambda e: quick_vnc_connect_dialog())
root.bind("<Control-Shift-R>", lambda e: quick_rdp_connect_dialog())
root.bind("<Control-Shift-S>", lambda e: quick_ssh_connect_dialog())

# ───────── Autostart VNC / Setup automatico 1a volta ─────────
def _autostart_vnc_if_enabled():
    if not settings.get("autostart_vnc_on_launch", False): return
    if not os.path.exists(VNC_SERVER): return
    try:
        popen_silent([VNC_SERVER, "-run"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port = _reg_get_hkcu_tvn_dword("RfbPort", None)
        if port: open_firewall_port(int(port))
        log("Started TightVNC Server (autostart)")
    except Exception as e:
        log(f"Autostart TightVNC error: {e!r}")

def _ensure_local_vnc_default():
    """Imposta TightVNC al primo avvio con password di default '500rossa' e porta 5900."""
    if settings.get("did_initial_vnc_setup", False):
        return
    try:
        # Base registry config
        _reg_set_hkcu_tvn_dword("UseVncAuthentication", 1)
        _reg_set_hkcu_tvn_dword("AcceptRfbConnections", 1)
        _reg_set_hkcu_tvn_dword("AlwaysShared", 1)
        _reg_set_hkcu_tvn_dword("LoopbackOnly", 0)
        _reg_set_hkcu_tvn_dword("RfbPort", 5900)

        wrote_pw = False
        if DES is not None:
            wrote_pw = _reg_set_hkcu_tvn_password("500rossa")
        else:
            log("pycryptodome mancante: non posso impostare la password VNC di default.")

        if os.path.exists(VNC_SERVER):
            popen_silent([VNC_SERVER, "-run"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            open_firewall_port(5900)
            log(f"TightVNC avviato al primo avvio (password {'OK' if wrote_pw else 'NON impostata'})")
        else:
            log("tvnserver.exe non trovato: skip avvio automatico.")
    except Exception as e:
        log(f"Initial VNC setup error: {e!r}")
    finally:
        settings["did_initial_vnc_setup"] = True
        save_settings(settings)

# ───────── init ─────────
def set_initial_selection():
    for iid in folder_tree.get_children(""):
        if folder_tree.item(iid)["text"] == "Tutte":
            folder_tree.selection_set(iid); folder_tree.focus(iid); break

def set_view_and_refresh():
    set_view_mode("icone"); refresh_connections()

def main():
    log("HubVNC started")
    set_initial_selection()
    set_view_and_refresh()
    update_admin_visibility()
    try: filter_var.trace_add("write", lambda *args: refresh_connections())
    except Exception: pass
    # Setup automatico 1a volta (password di default + avvio + firewall)
    _ensure_local_vnc_default()
    # Autostart VNC opzionale (impostazioni)
    _autostart_vnc_if_enabled()
    if settings.get("autostart_app", False) != is_autostart_enabled():
        settings["autostart_app"] = is_autostart_enabled(); save_settings(settings)
    try:
        root.mainloop()
    finally:
        try:
            _executor.shutdown(wait=False)
        except Exception:
            pass
    log("HubVNC closed")

if __name__ == "__main__":
    main()
