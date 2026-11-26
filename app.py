"""
Python Online Compiler - Flask + SocketIO + AI + Music Player
Theme: Neo-Brutalism
Port : 2009

Semua HTML inline di file ini. Cukup jalankan:
    pip install -r requirements.txt
    python app.py

Buka: http://localhost:2009
"""

import os
import re
import pty
import time
import json
import fcntl
import struct
import select
import signal
import subprocess
import termios
import hashlib
import threading
from functools import wraps
from datetime import datetime

import requests
from flask import (
    Flask, request, redirect, url_for, session,
    jsonify, send_file, abort, Response,
)
from flask_socketio import SocketIO, disconnect

# ======================================================================
# KONFIGURASI
# ======================================================================
HOST = "0.0.0.0"
PORT = 2009

SHELL_CMD = os.environ.get("PYTERM_SHELL", "bash")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LIBRARY_DIR = os.path.join(BASE_DIR, "saved_codes")
USERS_DIR = os.path.join(BASE_DIR, "users")
USER_FILES_DIR = os.path.join(BASE_DIR, "user_files")
MUSIC_CACHE_DIR = os.path.join(BASE_DIR, "music_cache")

for d in [LIBRARY_DIR, USERS_DIR, USER_FILES_DIR, MUSIC_CACHE_DIR]:
    os.makedirs(d, exist_ok=True)

# ======================================================================
# KODE AKSES AI (RAHASIA - hanya ada di sini)
# ======================================================================
ACCESS_CODE = "KELL"

# ======================================================================
# AI API CONFIG
# ======================================================================
UNLI_V1_BASE_URL = os.environ.get("UNLI_V1_BASE_URL", "https://api.unli.dev/v1")
UNLI_V1_API_KEY = os.environ.get(
    "UNLI_V1_API_KEY",
    "sk-gEZkEMD9rsta7bF58BWDwAoR3VUkTczLlpJVQdj_6i45bhXqGNwK4D2QC-uLXVZ7",
)
UNLI_V2_URL = os.environ.get("UNLI_V2_URL", "https://api.kyzznekoo.my.id/api/ai/unlimited")
CICI_URL = os.environ.get("CICI_URL", "https://api.kyzznekoo.my.id/api/ai/cici")
DEEPSEK_URL = os.environ.get("DEEPSEK_URL", "https://api.nexadev.my.id/ai/deepsek")
SPOTIFY_URL = os.environ.get("SPOTIFY_URL", "https://api.nexadev.my.id/api/spotifyplay")

COMMON_UA = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
)

# ======================================================================
# FLASK APP
# ======================================================================
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("PYTERM_SECRET", "pyterm-neo-brutalism-secret-change-me")
app.config["SESSION_COOKIE_SECURE"] = False
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = 86400 * 7

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="eventlet",
    ping_timeout=60,
    ping_interval=25,
)

sessions = {}
music_state = {}
music_lock = threading.Lock()
download_in_progress = {}
download_lock = threading.Lock()

# ======================================================================
# USER MANAGEMENT
# ======================================================================
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def get_user_dir(username):
    d = os.path.join(USER_FILES_DIR, username)
    os.makedirs(d, exist_ok=True)
    return d

def get_user_library_dir(username):
    d = os.path.join(get_user_dir(username), "library")
    os.makedirs(d, exist_ok=True)
    return d

def load_users():
    f = os.path.join(USERS_DIR, "users.json")
    if os.path.exists(f):
        try:
            with open(f) as fp:
                return json.load(fp)
        except Exception:
            return {}
    return {}

def save_users(users):
    f = os.path.join(USERS_DIR, "users.json")
    with open(f, "w") as fp:
        json.dump(users, fp, indent=2)

# ======================================================================
# AUTH HELPERS
# ======================================================================
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

# ======================================================================
# ROUTES: AUTH
# ======================================================================
@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        if not username or not password:
            error = "Username dan password harus diisi."
        elif len(username) < 3:
            error = "Username minimal 3 karakter."
        elif len(password) < 4:
            error = "Password minimal 4 karakter."
        elif password != confirm:
            error = "Konfirmasi password tidak cocok."
        else:
            users = load_users()
            if username in users:
                error = "Username sudah terdaftar."
            else:
                users[username] = {
                    "password": hash_password(password),
                    "created_at": datetime.now().isoformat()
                }
                save_users(users)
                get_user_dir(username)
                return redirect(url_for("login"))

    return Response(render_register_html(error), mimetype="text/html")

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")

        users = load_users()
        if username in users and users[username]["password"] == hash_password(password):
            session.permanent = True
            session["logged_in"] = True
            session["username"] = username
            return redirect(url_for("index"))
        error = "Username atau password salah."

    return Response(render_login_html(error), mimetype="text/html")

@app.route("/logout")
def logout():
    username = session.get("username")
    if username:
        stop_music(username)
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@login_required
def index():
    return Response(render_terminal_html(), mimetype="text/html")

@app.route("/manifest.json")
def manifest():
    return jsonify({
        "name": "Python Online Compiler",
        "short_name": "PyCompiler",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#FFEB3B",
        "theme_color": "#FFEB3B",
        "orientation": "any",
    })

# ======================================================================
# AI HELPERS
# ======================================================================
def extract_python_code(text):
    if not text:
        return None
    m = re.search(r"```python\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if not m:
        m = re.search(r"```\s*\n(.*?)```", text, re.DOTALL)
    return m.group(1).strip() if m else None

def extract_ai_text(data):
    if isinstance(data, str):
        return data
    if not isinstance(data, dict):
        return None
    paths = [
        ("result", "response"), ("result", "message"), ("result", "text"),
        ("result",), ("response",), ("message",),
        ("data", "response"), ("data", "message"), ("data",),
        ("answer",), ("content",), ("text",),
    ]
    for path in paths:
        node = data
        ok = True
        for key in path:
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                ok = False
                break
        if ok and isinstance(node, str) and node.strip():
            return node
    return None

def call_unli_v1(prompt):
    try:
        r = requests.post(
            f"{UNLI_V1_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {UNLI_V1_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"model": "auto", "messages": [{"role": "user", "content": prompt}], "stream": False},
            timeout=60,
        )
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"Error: {e}"

def call_unli_v2(prompt):
    try:
        r = requests.get(UNLI_V2_URL,
                         params={"text": prompt, "_": str(int(time.time() * 1000))},
                         headers={"User-Agent": COMMON_UA}, timeout=45)
        return extract_ai_text(r.json()) or "Tidak ada respon"
    except Exception as e:
        return f"Error: {e}"

def call_cici(prompt):
    try:
        r = requests.get(CICI_URL,
                         params={"prompt": prompt, "_": str(int(time.time() * 1000))},
                         headers={"User-Agent": COMMON_UA}, timeout=45)
        return extract_ai_text(r.json()) or "Tidak ada respon"
    except Exception as e:
        return f"Error: {e}"

def call_deepsek(prompt):
    try:
        r = requests.get(DEEPSEK_URL,
                         params={"text": prompt, "_": str(int(time.time() * 1000))},
                         headers={"User-Agent": COMMON_UA}, timeout=45)
        return extract_ai_text(r.json()) or "Tidak ada respon"
    except Exception as e:
        return f"Error: {e}"

AI_MODELS = {
    "unli_v1": call_unli_v1,
    "unli_v2": call_unli_v2,
    "cici": call_cici,
    "deepsek": call_deepsek,
}

# ======================================================================
# AI ROUTES
# ======================================================================
@app.route("/api/ai/verify", methods=["POST"])
@login_required
def api_ai_verify():
    data = request.get_json(silent=True) or {}
    code = str(data.get("code", "")).strip()
    if not code:
        return jsonify({"success": False, "error": "Kode kosong."}), 400
    if code == ACCESS_CODE:
        session["ai_access"] = True
        session.permanent = True
        return jsonify({"success": True, "message": "Akses AI diberikan."})
    return jsonify({"success": False, "error": "Kode akses salah."}), 403

@app.route("/api/ai/status")
@login_required
def api_ai_status():
    return jsonify({"has_access": bool(session.get("ai_access"))})

@app.route("/api/generate")
@login_required
def api_generate():
    prompt = request.args.get("prompt", "").strip()
    model = request.args.get("model", "unli_v1")
    bypass = request.args.get("bypass", "0") == "1"

    if not bypass and not session.get("ai_access"):
        return jsonify({
            "success": False,
            "error": "Akses AI belum diberikan.",
            "need_access": True,
        }), 403

    if not prompt:
        return jsonify({"success": False, "error": "Prompt kosong."}), 400

    handler = AI_MODELS.get(model)
    if handler is None:
        return jsonify({"success": False, "error": "Model tidak dikenal."}), 400

    try:
        ai_text = handler(prompt)
    except Exception as e:
        return jsonify({"success": False, "error": f"Error AI: {e}"}), 502

    if not ai_text or "Error:" in ai_text:
        return jsonify({"success": False, "error": f"AI gagal: {ai_text}"}), 502

    code = extract_python_code(ai_text)
    if not code:
        return jsonify({"success": False, "error": "Tidak ada blok ```python``` di jawaban AI."})

    return jsonify({"success": True, "code": code})

# ======================================================================
# MUSIC: CACHE + DOWNLOAD + PLAY
# ======================================================================
def safe_cache_key(text):
    key = re.sub(r"[^A-Za-z0-9_\-]", "_", text.lower().strip())
    key = re.sub(r"_+", "_", key).strip("_")
    return (key or "unknown")[:80]

def find_cached_music(query):
    key = safe_cache_key(query)
    audio_path = None
    for ext in [".mp3", ".m4a", ".webm", ".ogg", ".opus", ".wav"]:
        p = os.path.join(MUSIC_CACHE_DIR, f"{key}{ext}")
        if os.path.exists(p) and os.path.getsize(p) > 0:
            audio_path = p
            break
    meta_path = os.path.join(MUSIC_CACHE_DIR, f"{key}.json")
    meta = None
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as f:
                meta = json.load(f)
        except Exception:
            meta = None
    if audio_path and meta:
        return {"audio": audio_path, "meta": meta, "key": key}
    return None

def download_music_worker(query, key):
    try:
        r = requests.get(SPOTIFY_URL,
                         params={"q": query, "_": str(int(time.time() * 1000))},
                         headers={"User-Agent": COMMON_UA}, timeout=30)
        data = r.json()
        if not (data.get("status") and data.get("result")):
            return
        result = data["result"]
        download_url = result.get("download_url") or result.get("url")
        if not download_url:
            return

        audio_path = os.path.join(MUSIC_CACHE_DIR, f"{key}.mp3")
        with requests.get(download_url, stream=True, timeout=180,
                          headers={"User-Agent": COMMON_UA}) as resp:
            resp.raise_for_status()
            with open(audio_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)

        meta_path = os.path.join(MUSIC_CACHE_DIR, f"{key}.json")
        with open(meta_path, "w") as f:
            json.dump(result, f, indent=2)

        print(f"[Music] Downloaded: {result.get('title')} -> {key}")
    except Exception as e:
        print(f"[Music] Download error ({key}): {e}")
        try:
            bad = os.path.join(MUSIC_CACHE_DIR, f"{key}.mp3")
            if os.path.exists(bad) and os.path.getsize(bad) < 1000:
                os.remove(bad)
        except Exception:
            pass
    finally:
        with download_lock:
            download_in_progress.pop(key, None)

def get_audio_player():
    for name, cmd in [
        ("mpv", ["mpv", "--no-video", "--really-quiet"]),
        ("ffplay", ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]),
        ("mplayer", ["mplayer", "-novideo", "-really-quiet"]),
        ("cvlc", ["cvlc", "--play-and-exit", "--intf", "dummy"]),
    ]:
        try:
            subprocess.run(["which", name], capture_output=True, check=True)
            return name, cmd
        except Exception:
            continue
    return None, None

def play_audio_worker(username, audio_path, meta):
    player_name, player_cmd = get_audio_player()
    if not player_name:
        with music_lock:
            st = music_state.get(username)
            if st:
                st["playing"] = False
                st["status"] = "error"
                st["error"] = "Tidak ada player audio. Install: sudo apt install mpv"
        return

    try:
        process = subprocess.Popen(
            player_cmd + [audio_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        with music_lock:
            st = music_state.get(username)
            if st:
                st["process"] = process
                st["status"] = "playing"
        process.wait()
        with music_lock:
            st = music_state.get(username)
            if st and st.get("playing"):
                st["playing"] = False
                st["status"] = "finished"
    except Exception as e:
        with music_lock:
            st = music_state.get(username)
            if st:
                st["playing"] = False
                st["status"] = "error"
                st["error"] = str(e)

def stop_music(username):
    with music_lock:
        st = music_state.get(username)
        if not st:
            return
        st["playing"] = False
        proc = st.get("process")
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        st["process"] = None
        st["status"] = "stopped"

# ======================================================================
# MUSIC ROUTES
# ======================================================================
@app.route("/api/music/search")
@login_required
def api_music_search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"success": False, "error": "Query kosong."}), 400

    key = safe_cache_key(query)
    cached = find_cached_music(query)
    if cached:
        return jsonify({
            "success": True,
            "result": cached["meta"],
            "cached": True,
            "cache_key": key,
        })

    try:
        r = requests.get(SPOTIFY_URL,
                         params={"q": query, "_": str(int(time.time() * 1000))},
                         headers={"User-Agent": COMMON_UA}, timeout=30)
        data = r.json()
        if data.get("status") and data.get("result"):
            return jsonify({
                "success": True,
                "result": data["result"],
                "cached": False,
                "cache_key": key,
            })
        return jsonify({"success": False, "error": "Lagu tidak ditemukan."}), 404
    except Exception as e:
        return jsonify({"success": False, "error": f"Gagal search: {e}"}), 502

@app.route("/api/music/download", methods=["POST"])
@login_required
def api_music_download():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"success": False, "error": "Query kosong."}), 400

    key = safe_cache_key(query)
    cached = find_cached_music(query)
    if cached:
        return jsonify({
            "success": True,
            "ready": True,
            "cache_key": key,
            "result": cached["meta"],
        })

    with download_lock:
        if key in download_in_progress:
            return jsonify({
                "success": True,
                "ready": False,
                "downloading": True,
                "cache_key": key,
            })
        download_in_progress[key] = True

    threading.Thread(target=download_music_worker, args=(query, key), daemon=True).start()
    return jsonify({
        "success": True,
        "ready": False,
        "downloading": True,
        "cache_key": key,
    })

@app.route("/api/music/check")
@login_required
def api_music_check():
    key = request.args.get("key", "").strip()
    if not key:
        return jsonify({"success": False, "error": "Key kosong."}), 400

    audio_path = None
    for ext in [".mp3", ".m4a", ".webm", ".ogg", ".opus", ".wav"]:
        p = os.path.join(MUSIC_CACHE_DIR, f"{key}{ext}")
        if os.path.exists(p) and os.path.getsize(p) > 0:
            audio_path = p
            break

    meta_path = os.path.join(MUSIC_CACHE_DIR, f"{key}.json")
    meta = None
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as f:
                meta = json.load(f)
        except Exception:
            pass

    with download_lock:
        is_dl = key in download_in_progress

    if audio_path and meta:
        return jsonify({"success": True, "ready": True, "result": meta})
    if is_dl:
        return jsonify({"success": True, "ready": False, "downloading": True})
    return jsonify({"success": True, "ready": False, "downloading": False})

@app.route("/api/music/play", methods=["POST"])
@login_required
def api_music_play():
    username = session.get("username")
    if not username:
        return jsonify({"success": False, "error": "Session invalid."}), 401

    data = request.get_json(silent=True) or {}
    query = (data.get("query") or data.get("title") or "").strip()
    if not query:
        return jsonify({"success": False, "error": "Query kosong."}), 400

    key = safe_cache_key(query)
    cached = find_cached_music(query)
    if not cached:
        with download_lock:
            is_dl = key in download_in_progress
        if is_dl:
            return jsonify({"success": False, "error": "Masih download, tunggu..."}), 425
        with download_lock:
            download_in_progress[key] = True
        threading.Thread(target=download_music_worker, args=(query, key), daemon=True).start()
        return jsonify({"success": False, "error": "Download dimulai, coba lagi sebentar."}), 425

    stop_music(username)

    with music_lock:
        music_state[username] = {
            "playing": True,
            "process": None,
            "data": cached["meta"],
            "file": cached["audio"],
            "status": "loading",
            "error": None,
        }

    threading.Thread(
        target=play_audio_worker,
        args=(username, cached["audio"], cached["meta"]),
        daemon=True,
    ).start()

    return jsonify({
        "success": True,
        "message": f"Memutar: {cached['meta'].get('title', 'Unknown')}",
        "data": cached["meta"],
    })

@app.route("/api/music/stop")
@login_required
def api_music_stop():
    username = session.get("username")
    if username:
        stop_music(username)
    return jsonify({"success": True, "message": "Musik dihentikan."})

@app.route("/api/music/status")
@login_required
def api_music_status():
    username = session.get("username")
    with music_lock:
        st = music_state.get(username)
        if st:
            return jsonify({
                "playing": st.get("playing", False),
                "data": st.get("data", {}),
                "status": st.get("status", ""),
                "error": st.get("error"),
            })
    return jsonify({"playing": False, "status": "idle"})

# ======================================================================
# LIBRARY FILES (per user)
# ======================================================================
def safe_filename(raw_name):
    if not raw_name:
        return None
    name = re.sub(r"[^A-Za-z0-9 _\-\.]", "", raw_name).strip()
    name = name.replace(" ", "_")
    if not name:
        return None
    if not name.lower().endswith(".py"):
        name += ".py"
    return name

@app.route("/api/files/save", methods=["POST"])
@login_required
def api_files_save():
    username = session.get("username")
    payload = request.get_json(silent=True) or {}
    filename = safe_filename(payload.get("name", ""))
    code = payload.get("code", "")

    if not filename:
        return jsonify({"success": False, "error": "Nama file tidak valid."}), 400

    user_lib_dir = get_user_library_dir(username)
    path = os.path.join(user_lib_dir, filename)
    if not os.path.abspath(path).startswith(os.path.abspath(user_lib_dir)):
        return jsonify({"success": False, "error": "Path invalid."}), 400

    try:
        with open(path, "w") as f:
            f.write(code)
    except OSError as e:
        return jsonify({"success": False, "error": f"Gagal simpan: {e}"}), 500

    return jsonify({"success": True, "name": filename})

@app.route("/api/files/list")
@login_required
def api_files_list():
    username = session.get("username")
    user_lib_dir = get_user_library_dir(username)
    files = []
    for fname in sorted(os.listdir(user_lib_dir)):
        if not fname.lower().endswith(".py"):
            continue
        fpath = os.path.join(user_lib_dir, fname)
        if not os.path.isfile(fpath):
            continue
        stat = os.stat(fpath)
        files.append({
            "name": fname,
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    files.sort(key=lambda f: f["modified"], reverse=True)
    return jsonify({"success": True, "files": files})

@app.route("/api/files/content")
@login_required
def api_files_content():
    username = session.get("username")
    filename = safe_filename(request.args.get("name", ""))
    if not filename:
        return jsonify({"success": False, "error": "Nama tidak valid."}), 400

    user_lib_dir = get_user_library_dir(username)
    path = os.path.join(user_lib_dir, filename)
    if not os.path.isfile(path):
        return jsonify({"success": False, "error": "File tidak ditemukan."}), 404

    with open(path, "r") as f:
        content = f.read()
    return jsonify({"success": True, "name": filename, "code": content})

@app.route("/api/files/download")
@login_required
def api_files_download():
    username = session.get("username")
    filename = safe_filename(request.args.get("name", ""))
    if not filename:
        abort(400)
    user_lib_dir = get_user_library_dir(username)
    path = os.path.join(user_lib_dir, filename)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=filename)

# ======================================================================
# PTY / TERMINAL
# ======================================================================
def set_winsize(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

def read_and_forward_output(sid):
    proc = sessions.get(sid)
    if not proc:
        return
    fd = proc["fd"]
    while True:
        socketio.sleep(0.01)
        if sid not in sessions:
            break
        try:
            (ready, _, _) = select.select([fd], [], [], 0)
            if ready:
                output = os.read(fd, 1024 * 20)
                socketio.emit("terminal_output", {"output": output.decode(errors="ignore")}, to=sid)
        except OSError:
            break

def build_shell_command_and_env():
    env = os.environ.copy()
    env.setdefault("HOME", "/tmp")
    env.setdefault("USER", "user")
    if SHELL_CMD == "bash":
        env["PS1"] = "$ "
        return ["bash", "--noprofile", "--norc"], env
    return [SHELL_CMD], env

@socketio.on("connect")
def on_connect():
    if not session.get("logged_in"):
        disconnect()
        return
    sid = request.sid
    if sid in sessions:
        return
    child_pid, fd = pty.fork()
    if child_pid == 0:
        cmd, cenv = build_shell_command_and_env()
        subprocess.run(cmd, env=cenv)
        os._exit(0)
    else:
        set_winsize(fd, 24, 80)
        sessions[sid] = {"fd": fd, "pid": child_pid}
        socketio.start_background_task(target=read_and_forward_output, sid=sid)

@socketio.on("disconnect")
def on_disconnect():
    sid = request.sid
    proc = sessions.pop(sid, None)
    if proc:
        try:
            os.kill(proc["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass

@socketio.on("terminal_input")
def on_terminal_input(data):
    if not session.get("logged_in"):
        disconnect()
        return
    sid = request.sid
    proc = sessions.get(sid)
    if proc:
        os.write(proc["fd"], data["input"].encode())

@socketio.on("terminal_resize")
def on_terminal_resize(data):
    sid = request.sid
    proc = sessions.get(sid)
    if proc:
        try:
            set_winsize(proc["fd"], data["rows"], data["cols"])
        except Exception:
            pass

@socketio.on("run_code")
def on_run_code(data):
    if not session.get("logged_in"):
        disconnect()
        return
    sid = request.sid
    proc = sessions.get(sid)
    if not proc:
        return
    code = data.get("code", "")
    script_path = f"/tmp/pyterm_{sid}.py"
    try:
        with open(script_path, "w") as f:
            f.write(code)
    except OSError:
        socketio.emit("terminal_output", {"output": "\r\n[Gagal simpan]\r\n"}, to=sid)
        return
    os.write(proc["fd"], f"clear; python3 -u {script_path}\n".encode())

@socketio.on("terminal_signal")
def on_terminal_signal(data):
    if not session.get("logged_in"):
        disconnect()
        return
    sid = request.sid
    proc = sessions.get(sid)
    if proc and data.get("signal") == "SIGINT":
        os.write(proc["fd"], b"\x03")

# ======================================================================
# HTML RENDERERS (NEO-BRUTALISM)
# ======================================================================
NEO_CSS = """
:root {
  --yellow: #FFEB3B;
  --pink: #FF6EC7;
  --cyan: #00E5FF;
  --green: #00E676;
  --red: #FF3B30;
  --purple: #B388FF;
  --orange: #FF9800;
  --black: #000000;
  --white: #FFFFFF;
  --bg: #FFF9C4;
  --panel: #FFFFFF;
  --text: #000000;
  --border: 3px solid #000000;
  --shadow-sm: 4px 4px 0 #000000;
  --shadow-md: 6px 6px 0 #000000;
  --shadow-lg: 8px 8px 0 #000000;
}
html[data-theme="dark"] {
  --bg: #1A1A1A;
  --panel: #2A2A2A;
  --text: #FFFFFF;
  --border: 3px solid #FFFFFF;
  --shadow-sm: 4px 4px 0 #FFFFFF;
  --shadow-md: 6px 6px 0 #FFFFFF;
  --shadow-lg: 8px 8px 0 #FFFFFF;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body {
  height: 100%; width: 100%; overflow-x: hidden;
  font-family: 'Courier New', 'Courier', monospace;
  font-weight: 700;
  background: var(--bg);
  color: var(--text);
  transition: background 0.2s, color 0.2s;
}

/* ============ HEADER ============ */
.nb-header {
  background: var(--yellow);
  border-bottom: var(--border);
  padding: 12px 20px;
  display: flex; align-items: center; justify-content: space-between;
  gap: 10px; flex-wrap: wrap;
}
html[data-theme="dark"] .nb-header { background: var(--purple); }
.nb-header h1 {
  font-size: 18px; font-weight: 900;
  text-transform: uppercase; letter-spacing: 1px;
  display: flex; align-items: center; gap: 10px;
}
.nb-status-dot {
  width: 14px; height: 14px;
  background: var(--green);
  border: 2px solid var(--black);
  border-radius: 50%;
  display: inline-block;
}
.nb-header-actions { display: flex; align-items: center; gap: 8px; }

/* ============ BUTTONS ============ */
.nb-btn {
  background: var(--white);
  color: var(--black);
  border: var(--border);
  border-radius: 0;
  padding: 10px 16px;
  font-family: inherit;
  font-size: 13px; font-weight: 900;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  cursor: pointer;
  box-shadow: var(--shadow-sm);
  transition: transform 0.08s, box-shadow 0.08s;
  display: inline-flex; align-items: center; gap: 6px;
  text-decoration: none;
}
.nb-btn:hover { transform: translate(-2px, -2px); box-shadow: var(--shadow-md); }
.nb-btn:active { transform: translate(2px, 2px); box-shadow: 0 0 0 #000; }
.nb-btn.icon { padding: 8px 12px; font-size: 16px; }
.nb-btn.yellow { background: var(--yellow); }
.nb-btn.pink { background: var(--pink); color: var(--white); }
.nb-btn.cyan { background: var(--cyan); }
.nb-btn.green { background: var(--green); color: var(--white); }
.nb-btn.red { background: var(--red); color: var(--white); }
.nb-btn.purple { background: var(--purple); }
.nb-btn.orange { background: var(--orange); color: var(--white); }
.nb-btn.sm { padding: 6px 10px; font-size: 11px; }
.nb-btn:disabled { opacity: 0.6; cursor: not-allowed; }

/* ============ TOOLBAR ============ */
.nb-toolbar {
  display: flex; gap: 10px; flex-wrap: wrap; align-items: center;
  padding: 14px 20px;
  background: var(--panel);
  border-bottom: var(--border);
}

/* ============ MAIN LAYOUT ============ */
.nb-main {
  display: flex; flex-direction: row;
  height: calc(100vh - 180px);
  padding: 16px; gap: 0;
}
.nb-panel {
  display: grid; grid-template-rows: auto 1fr;
  flex: 1 1 0; min-width: 120px; min-height: 0;
  background: var(--panel);
  border: var(--border);
  box-shadow: var(--shadow-md);
  overflow: hidden;
}
.nb-panel-title {
  background: var(--black);
  color: var(--white);
  font-size: 12px; font-weight: 900;
  text-transform: uppercase; letter-spacing: 1px;
  padding: 10px 14px;
  display: flex; align-items: center; justify-content: space-between;
  border-bottom: var(--border);
}
.nb-panel-title .title-actions { display: flex; gap: 6px; }
.nb-expand-btn {
  background: var(--yellow); color: var(--black);
  border: 2px solid var(--white);
  width: 26px; height: 24px;
  font-size: 13px; font-weight: 900;
  cursor: pointer;
  display: flex; align-items: center; justify-content: center;
}
.nb-expand-btn:hover { background: var(--pink); }
.nb-panel-content { min-height: 0; overflow: hidden; position: relative; }
.CodeMirror {
  width: 100% !important; height: 100% !important;
  font-size: 14px;
  font-family: 'Courier New', monospace !important;
  font-weight: 700;
}
#output-panel { background: var(--black); }
#output-panel .nb-panel-title { background: var(--cyan); color: var(--black); }
#output-panel .nb-panel-content { padding: 8px; }
#terminal { width: 100%; height: 100%; }

/* ============ SPLITTER ============ */
.nb-splitter {
  flex: 0 0 22px;
  display: flex; align-items: center; justify-content: center;
  cursor: col-resize; position: relative;
}
.nb-splitter-btn {
  width: 34px; height: 34px;
  background: var(--pink);
  border: var(--border);
  border-radius: 50%;
  box-shadow: var(--shadow-sm);
  cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  font-size: 16px; font-weight: 900;
  user-select: none;
  color: var(--white);
}
.nb-splitter-btn:hover { background: var(--orange); }

@media (max-width: 800px) {
  .nb-main { flex-direction: column; height: auto; }
  .nb-splitter { flex: 0 0 30px; cursor: row-resize; }
  .nb-splitter-btn { transform: rotate(90deg); }
  .nb-header h1 { font-size: 14px; }
  .nb-btn { font-size: 11px; padding: 8px 10px; }
}

/* ============ MODAL ============ */
.nb-modal-overlay {
  display: none; position: fixed; inset: 0;
  background: rgba(0,0,0,0.7);
  z-index: 999;
  align-items: center; justify-content: center;
  padding: 20px;
}
.nb-modal-card {
  background: var(--panel);
  border: var(--border);
  box-shadow: var(--shadow-lg);
  padding: 24px;
  width: 100%; max-width: 500px;
  max-height: 90vh; overflow-y: auto;
}
.nb-modal-card.wide { max-width: 620px; }
.nb-modal-card h3 {
  font-size: 20px; font-weight: 900;
  text-transform: uppercase;
  margin-bottom: 10px;
  background: var(--yellow);
  display: inline-block;
  padding: 4px 12px;
  border: var(--border);
  box-shadow: var(--shadow-sm);
}
html[data-theme="dark"] .nb-modal-card h3 { background: var(--purple); }
.nb-modal-desc {
  font-size: 12.5px; margin: 12px 0 14px;
  line-height: 1.5;
  font-weight: 700;
}

.nb-input, .nb-select, .nb-textarea {
  width: 100%;
  background: var(--white);
  color: var(--black);
  border: var(--border);
  box-shadow: var(--shadow-sm);
  padding: 12px 14px;
  font-family: inherit;
  font-size: 14px; font-weight: 700;
  outline: none;
  margin-top: 8px;
  border-radius: 0;
}
html[data-theme="dark"] .nb-input,
html[data-theme="dark"] .nb-select,
html[data-theme="dark"] .nb-textarea {
  background: #1A1A1A;
  color: var(--white);
  border: 3px solid var(--white);
  box-shadow: 4px 4px 0 var(--white);
}
.nb-input:focus, .nb-select:focus, .nb-textarea:focus {
  background: var(--cyan);
  color: var(--black);
}
.nb-textarea { min-height: 100px; resize: vertical; }

.nb-modal-status {
  min-height: 20px;
  font-size: 12.5px; font-weight: 800;
  margin-top: 10px;
}
.nb-modal-actions {
  display: flex; justify-content: flex-end; gap: 10px;
  margin-top: 18px; flex-wrap: wrap;
}

/* ============ MUSIC ============ */
.nb-music-result {
  margin-top: 14px; padding: 14px;
  background: var(--white);
  border: var(--border);
  box-shadow: var(--shadow-sm);
  display: none;
}
html[data-theme="dark"] .nb-music-result {
  background: #1A1A1A; border: 3px solid var(--white);
  box-shadow: 4px 4px 0 var(--white);
}
.nb-music-result.show { display: block; }
.nb-music-result img {
  max-width: 140px;
  border: var(--border);
  box-shadow: var(--shadow-sm);
}
.nb-music-result .track-info { margin-top: 12px; }
.nb-music-result .track-info .title {
  font-size: 17px; font-weight: 900; text-transform: uppercase;
}
.nb-music-result .track-info .artist {
  font-size: 14px; margin-top: 4px;
}
.nb-music-result .track-info .duration {
  font-size: 12px; margin-top: 6px;
  background: var(--cyan);
  color: var(--black);
  display: inline-block;
  padding: 2px 8px;
  border: 2px solid var(--black);
  font-weight: 900;
}
html[data-theme="dark"] .nb-music-result .track-info .duration {
  border: 2px solid var(--white);
}

/* ============ SPINNER ============ */
.nb-spinner {
  display: inline-block;
  width: 18px; height: 18px;
  border: 3px solid var(--black);
  border-top-color: var(--pink);
  border-radius: 50%;
  animation: nb-spin 0.7s linear infinite;
  vertical-align: middle;
  margin-right: 8px;
}
@keyframes nb-spin { to { transform: rotate(360deg); } }

/* ============ PROGRESS ============ */
.nb-progress-box {
  margin-top: 14px; padding: 12px;
  background: var(--yellow);
  border: var(--border);
  box-shadow: var(--shadow-sm);
  display: none;
}
html[data-theme="dark"] .nb-progress-box { background: var(--purple); }
.nb-progress-box.show { display: block; }
.nb-progress-label {
  font-size: 13px; font-weight: 900;
  display: flex; align-items: center;
  margin-bottom: 10px;
  text-transform: uppercase;
}
.nb-progress-bar-bg {
  height: 12px;
  background: var(--white);
  border: 2px solid var(--black);
  overflow: hidden;
}
html[data-theme="dark"] .nb-progress-bar-bg { border: 2px solid var(--white); }
.nb-progress-bar-fill {
  height: 100%; width: 40%;
  background: var(--pink);
  animation: nb-slide 1.2s ease-in-out infinite;
}
@keyframes nb-slide {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(300%); }
}

.nb-current-music {
  margin-top: 14px; padding: 12px;
  background: var(--cyan);
  color: var(--black);
  border: var(--border);
  box-shadow: var(--shadow-sm);
  font-size: 13px; font-weight: 900;
  display: flex; align-items: center; gap: 10px;
  text-transform: uppercase;
}
html[data-theme="dark"] .nb-current-music {
  background: var(--green); color: var(--black);
}

/* ============ LIBRARY ============ */
.nb-lib-item {
  display: flex; justify-content: space-between; align-items: center;
  gap: 10px; padding: 12px 10px;
  border-bottom: var(--border);
  background: var(--white);
  margin-bottom: 8px;
  box-shadow: var(--shadow-sm);
}
html[data-theme="dark"] .nb-lib-item {
  background: #1A1A1A; border: 3px solid var(--white);
  box-shadow: 4px 4px 0 var(--white);
}
.nb-lib-item:last-child { border-bottom: var(--border); }
.nb-lib-item-info { min-width: 0; flex: 1; }
.nb-lib-item-name {
  font-weight: 900; font-size: 14px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.nb-lib-item-meta {
  font-size: 11px; margin-top: 2px; font-weight: 700;
}
.nb-lib-item-actions {
  display: flex; gap: 6px; flex-shrink: 0;
}
.nb-lib-item-actions a { text-decoration: none; }

/* ============ LOGIN / REGISTER ============ */
.nb-auth-wrap {
  min-height: 100vh;
  display: flex; align-items: center; justify-content: center;
  padding: 20px;
  background: var(--yellow);
}
html[data-theme="dark"] .nb-auth-wrap { background: var(--bg); }
.nb-auth-card {
  background: var(--white);
  border: 4px solid var(--black);
  box-shadow: 10px 10px 0 var(--black);
  padding: 40px 32px;
  width: 100%; max-width: 420px;
}
html[data-theme="dark"] .nb-auth-card {
  background: #1A1A1A;
  border: 4px solid var(--white);
  box-shadow: 10px 10px 0 var(--white);
}
.nb-auth-card .logo {
  text-align: center; font-size: 48px;
  margin-bottom: 10px;
}
.nb-auth-card h2 {
  text-align: center;
  font-size: 24px; font-weight: 900;
  text-transform: uppercase;
  letter-spacing: 1px;
  margin-bottom: 24px;
  background: var(--pink);
  color: var(--white);
  padding: 10px;
  border: var(--border);
  box-shadow: var(--shadow-sm);
}
.nb-auth-card label {
  display: block;
  font-size: 12px; font-weight: 900;
  text-transform: uppercase;
  letter-spacing: 1px;
  margin: 16px 0 6px;
}
.nb-auth-card button {
  width: 100%; margin-top: 24px;
  padding: 14px;
  background: var(--green);
  color: var(--white);
  border: var(--border);
  box-shadow: var(--shadow-md);
  font-family: inherit;
  font-size: 16px; font-weight: 900;
  text-transform: uppercase; letter-spacing: 1px;
  cursor: pointer;
  transition: transform 0.08s, box-shadow 0.08s;
}
.nb-auth-card button:hover {
  transform: translate(-2px, -2px);
  box-shadow: var(--shadow-lg);
}
.nb-auth-card button:active {
  transform: translate(2px, 2px);
  box-shadow: 0 0 0 #000;
}
.nb-error-msg {
  background: var(--red);
  color: var(--white);
  border: var(--border);
  box-shadow: var(--shadow-sm);
  padding: 12px 14px;
  font-size: 13px; font-weight: 900;
  margin-bottom: 10px;
  text-transform: uppercase;
}
.nb-auth-link {
  text-align: center; margin-top: 20px;
  font-size: 13px; font-weight: 900;
  text-transform: uppercase;
}
.nb-auth-link a {
  color: var(--black);
  text-decoration: none;
  background: var(--cyan);
  padding: 4px 10px;
  border: 2px solid var(--black);
  box-shadow: 2px 2px 0 var(--black);
  display: inline-block;
  margin-left: 6px;
}
html[data-theme="dark"] .nb-auth-link a {
  color: var(--black);
}
.nb-auth-link a:hover {
  background: var(--pink);
  color: var(--white);
}

kbd {
  background: var(--black);
  color: var(--white);
  padding: 3px 8px;
  font-family: inherit;
  font-size: 12px;
  border: 2px solid var(--white);
  font-weight: 900;
}
"""

# ======================================================================
# LOGIN HTML
# ======================================================================
def render_login_html(error=None):
    err = f'<div class="nb-error-msg">⚠ {error}</div>' if error else ''
    return f"""<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Login · Python Online Compiler</title>
<meta name="theme-color" content="#FFEB3B">
<style>{NEO_CSS}</style>
</head>
<body>
<div class="nb-auth-wrap">
  <div class="nb-auth-card">
    <div class="logo">⚫</div>
    <h2>Py Compiler</h2>
    {err}
    <form method="POST" action="/login">
      <label>Username</label>
      <input class="nb-input" type="text" name="username" required autofocus>
      <label>Password</label>
      <input class="nb-input" type="password" name="password" required>
      <button type="submit">▶ Masuk</button>
    </form>
    <div class="nb-auth-link">
      Belum punya akun? <a href="/register">Daftar</a>
    </div>
  </div>
</div>
</body>
</html>"""

# ======================================================================
# REGISTER HTML
# ======================================================================
def render_register_html(error=None):
    err = f'<div class="nb-error-msg">⚠ {error}</div>' if error else ''
    return f"""<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Daftar · Python Online Compiler</title>
<meta name="theme-color" content="#FFEB3B">
<style>{NEO_CSS}</style>
</head>
<body>
<div class="nb-auth-wrap">
  <div class="nb-auth-card">
    <div class="logo">⚫</div>
    <h2>Daftar Akun</h2>
    {err}
    <form method="POST" action="/register">
      <label>Username</label>
      <input class="nb-input" type="text" name="username" required autofocus minlength="3">
      <label>Password</label>
      <input class="nb-input" type="password" name="password" required minlength="4">
      <label>Konfirmasi Password</label>
      <input class="nb-input" type="password" name="confirm" required minlength="4">
      <button type="submit">✚ Daftar</button>
    </form>
    <div class="nb-auth-link">
      Sudah punya akun? <a href="/login">Masuk</a>
    </div>
  </div>
</div>
</body>
</html>"""

# ======================================================================
# TERMINAL HTML (MAIN APP)
# ======================================================================
def render_terminal_html():
    return """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Python Online Compiler · Neo-Brutalism</title>
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#FFEB3B">
<script>
  (function(){try{var t=localStorage.getItem('pyterm-theme');if(t==='dark')document.documentElement.setAttribute('data-theme','dark');}catch(e){}})();
</script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css" />
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/codemirror.min.css" />
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/theme/material-darker.min.css" />
<style>""" + NEO_CSS + """</style>
</head>
<body>

<header class="nb-header">
  <h1><span class="nb-status-dot" id="statusDot"></span> Python Compiler</h1>
  <div class="nb-header-actions">
    <button class="nb-btn icon yellow" id="themeToggleBtn" onclick="toggleTheme()" title="Tema">🌙</button>
    <button class="nb-btn icon cyan" onclick="openLibrary()" title="Library">📚</button>
    <button class="nb-btn icon pink" id="musicToggleBtn" onclick="toggleMusicModal()" title="Musik (Ctrl+E)">🎵</button>
    <button class="nb-btn icon" id="aiToggleBtn" onclick="handleAiButton()" title="AI Generator">🤖 🔒</button>
    <a class="nb-btn icon red" href="/logout" title="Keluar">⏻</a>
  </div>
</header>

<div class="nb-toolbar">
  <button class="nb-btn green" onclick="runCode()">▶ Run</button>
  <button class="nb-btn yellow" onclick="clearOutput()">🧹 Clear Output</button>
  <button class="nb-btn orange" onclick="clearCode()">🗑 Clear Kode</button>
  <button class="nb-btn cyan" onclick="saveCurrentFile()">💾 Simpan</button>
  <button class="nb-btn pink" onclick="openNewTab()">🗗 Tab Baru</button>
</div>

<div class="nb-main" id="main-layout">
  <div class="nb-panel" id="editor-panel">
    <div class="nb-panel-title">
      <span>📝 Editor · main.py</span>
      <div class="title-actions">
        <button class="nb-expand-btn" onclick="toggleMaximize('editor')">⤢</button>
      </div>
    </div>
    <div class="nb-panel-content"><textarea id="code-editor"></textarea></div>
  </div>

  <div class="nb-splitter" id="splitter">
    <div class="nb-splitter-btn" id="splitterBtn">⇔</div>
  </div>

  <div class="nb-panel" id="output-panel">
    <div class="nb-panel-title">
      <span>⚡ Output / Terminal</span>
      <div class="title-actions">
        <button class="nb-expand-btn" onclick="clearOutput()">🧹</button>
        <button class="nb-expand-btn" onclick="toggleMaximize('output')">⤢</button>
      </div>
    </div>
    <div class="nb-panel-content"><div id="terminal"></div></div>
  </div>
</div>

<!-- AI MODAL -->
<div class="nb-modal-overlay" id="ai-modal" onclick="if(event.target===this) closeAiModal()">
  <div class="nb-modal-card">
    <h3 id="ai-modal-title">🤖 Generate Kode</h3>
    <p class="nb-modal-desc" id="ai-modal-desc">
      Tulis permintaan kode Python. Kalau ada kata "tambah", kode akan ditambahkan ke yang sudah ada.
    </p>

    <div id="ai-access-panel" style="display:none;">
      <p class="nb-modal-desc" style="color:#FF3B30;">
        🔒 Butuh kode akses. Atau pakai <kbd>Ctrl+K</kbd> untuk akses cepat.
      </p>
      <input class="nb-input" type="password" id="ai-access-input" placeholder="Kode akses..."
             onkeydown="if(event.key==='Enter') submitAccessCode()">
      <div class="nb-modal-status" id="ai-access-status"></div>
      <div class="nb-modal-actions">
        <button class="nb-btn" onclick="closeAiModal()">Batal</button>
        <button class="nb-btn green" onclick="submitAccessCode()">🔓 Verifikasi</button>
      </div>
    </div>

    <div id="ai-generate-panel" style="display:none;">
      <select class="nb-select" id="ai-model">
        <option value="unli_v1">Unli AI v1</option>
        <option value="unli_v2">Unli AI v2</option>
        <option value="cici">Cici AI</option>
        <option value="deepsek">Deepsek AI</option>
      </select>
      <textarea class="nb-textarea" id="ai-prompt" placeholder="Contoh: buatkan kode python menghitung faktorial"></textarea>
      <div class="nb-modal-status" id="ai-status"></div>
      <div class="nb-modal-actions">
        <button class="nb-btn" onclick="closeAiModal()">Batal</button>
        <button class="nb-btn green" id="ai-generate-btn" onclick="generateAiCode()">⚡ Generate</button>
      </div>
    </div>
  </div>
</div>

<!-- MUSIC MODAL -->
<div class="nb-modal-overlay" id="music-modal" onclick="if(event.target===this) closeMusicModal()">
  <div class="nb-modal-card">
    <h3>🎵 Music Player</h3>
    <p class="nb-modal-desc">
      Cari lagu → tunggu download selesai → putar. Musik tetap jalan meski modal ditutup.
      Toggle dengan <kbd>Ctrl+E</kbd>.
    </p>

    <div style="display:flex; gap:8px; margin-bottom:12px; flex-wrap:wrap;">
      <input class="nb-input" type="text" id="music-search-input" placeholder="Judul lagu..." style="flex:1; min-width:180px; margin-top:0;"
             onkeydown="if(event.key==='Enter') searchMusic()">
      <button class="nb-btn pink" onclick="searchMusic()">🔍 Cari</button>
    </div>

    <div class="nb-music-result" id="music-result">
      <div id="music-result-content"></div>

      <div class="nb-progress-box" id="music-progress">
        <div class="nb-progress-label">
          <span class="nb-spinner"></span>
          <span id="music-progress-text">Menyiapkan download...</span>
        </div>
        <div class="nb-progress-bar-bg">
          <div class="nb-progress-bar-fill"></div>
        </div>
      </div>

      <div style="margin-top:14px; display:flex; gap:8px; flex-wrap:wrap;">
        <button class="nb-btn green" id="music-play-btn" onclick="playMusic()" disabled>⏳ Menunggu...</button>
        <button class="nb-btn red" onclick="stopMusic()">⏹ Stop</button>
      </div>
      <div class="nb-modal-status" id="music-status"></div>
    </div>

    <div class="nb-current-music" id="current-music-box" style="display:none;">
      <span id="current-music-icon">⏸</span>
      <span id="current-music-text">Tidak ada lagu diputar</span>
    </div>

    <div class="nb-modal-actions">
      <button class="nb-btn" onclick="closeMusicModal()">✕ Tutup</button>
    </div>
  </div>
</div>

<!-- LIBRARY MODAL -->
<div class="nb-modal-overlay" id="library-modal" onclick="if(event.target===this) closeLibrary()">
  <div class="nb-modal-card wide">
    <h3>📚 Library Kode</h3>
    <p class="nb-modal-desc">File yang kamu simpan (per akun, tidak tercampur user lain).</p>
    <div id="library-list"></div>
    <div class="nb-modal-actions">
      <button class="nb-btn" onclick="closeLibrary()">✕ Tutup</button>
    </div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.js"></script>
<script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.js"></script>
<script src="https://cdn.jsdelivr.net/npm/socket.io-client@4.7.5/dist/socket.io.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/codemirror.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/mode/python/python.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/addon/edit/closebrackets.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/addon/edit/matchbrackets.min.js"></script>

<script>
// ---------- STATE ----------
let hasAiAccess = false;
let currentFileName = null;
let musicData = null;
let musicCacheKey = null;
let isMusicPlaying = false;
let downloadPollTimer = null;
let currentAiMode = 'access';

// ---------- EDITOR ----------
const editor = CodeMirror.fromTextArea(document.getElementById('code-editor'), {
  mode: 'python', theme: 'default', lineNumbers: true,
  indentUnit: 4, tabSize: 4, indentWithTabs: false,
  lineWrapping: false, autofocus: true,
  autoCloseBrackets: true, matchBrackets: true,
});
editor.setValue('print("Hello, dunia!")\\nnama = input("Siapa namamu? ")\\nprint(f"Halo, {nama}!")\\n');

// ---------- TERMINAL ----------
const term = new Terminal({
  cursorBlink: true, fontSize: 14,
  fontFamily: '"Courier New", monospace',
  theme: { background: '#000000', foreground: '#00E5FF', cursor: '#FF6EC7', selectionBackground: '#FF6EC755' },
});
const fitAddon = new FitAddon.FitAddon();
term.loadAddon(fitAddon);
term.open(document.getElementById('terminal'));
fitAddon.fit();

const socket = io();
const statusDot = document.getElementById('statusDot');
socket.on('connect', () => { statusDot.style.background = '#00E676'; sendResize(); });
socket.on('disconnect', () => { statusDot.style.background = '#FF3B30'; });
socket.on('terminal_output', d => term.write(d.output));
term.onData(d => socket.emit('terminal_input', { input: d }));

let lastSentRows = null, lastSentCols = null;
function sendResize() {
  const r = term.rows, c = term.cols;
  if (r === lastSentRows && c === lastSentCols) return;
  lastSentRows = r; lastSentCols = c;
  socket.emit('terminal_resize', { rows: r, cols: c });
}

// ---------- SPLITTER ----------
const mainLayout = document.getElementById('main-layout');
const editorPanel = document.getElementById('editor-panel');
const outputPanelEl = document.getElementById('output-panel');
const splitter = document.getElementById('splitter');

let forceSideBySide = (localStorage.getItem('pyterm-force-row') === '1');
function isStacked() { return window.innerWidth <= 800; }

function refreshAll() {
  fitAddon.fit();
  editor.setSize('100%', '100%');
  editor.refresh();
  sendResize();
}
function applyFraction(fraction) {
  fraction = Math.min(0.85, Math.max(0.15, fraction));
  if (isStacked()) {
    const total = mainLayout.getBoundingClientRect().height;
    editorPanel.style.flex = '0 0 auto';
    editorPanel.style.height = Math.round(total * fraction) + 'px';
    outputPanelEl.style.flex = '0 0 auto';
    outputPanelEl.style.height = Math.round(total * (1 - fraction)) + 'px';
  } else {
    editorPanel.style.flex = `0 0 ${fraction * 100}%`;
    outputPanelEl.style.flex = '1 1 auto';
  }
  refreshAll();
}
let dragging = false;
splitter.addEventListener('mousedown', () => { dragging = true; document.body.style.userSelect = 'none'; });
splitter.addEventListener('touchstart', () => { dragging = true; }, { passive: true });
function onDrag(e) {
  if (!dragging) return;
  const p = e.touches ? e.touches[0] : e;
  const rect = mainLayout.getBoundingClientRect();
  const fraction = isStacked()
    ? (p.clientY - rect.top) / rect.height
    : (p.clientX - rect.left) / rect.width;
  applyFraction(fraction);
}
function stopDrag() { dragging = false; document.body.style.userSelect = ''; }
window.addEventListener('mousemove', onDrag);
window.addEventListener('touchmove', onDrag, { passive: true });
window.addEventListener('mouseup', stopDrag);
window.addEventListener('touchend', stopDrag);

function setDefaultSizes() {
  if (isStacked()) {
    editorPanel.style.flex = '0 0 auto';
    editorPanel.style.height = '420px';
    outputPanelEl.style.flex = '0 0 auto';
    outputPanelEl.style.height = '320px';
  } else {
    editorPanel.style.flex = '0 0 50%';
    outputPanelEl.style.flex = '1 1 auto';
  }
  refreshAll();
}
window.addEventListener('resize', setDefaultSizes);

// ---------- TOOLBAR ----------
function runCode() {
  socket.emit('run_code', { code: editor.getValue() });
  term.focus();
}
function clearOutput() { term.clear(); }
function clearCode() { editor.setValue(''); currentFileName = null; editor.focus(); }
function openNewTab() { window.open(window.location.origin + '/', '_blank'); }

// ---------- THEME ----------
function applyThemeIcon() {
  const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
  document.getElementById('themeToggleBtn').textContent = isDark ? '☀️' : '🌙';
  editor.setOption('theme', isDark ? 'material-darker' : 'default');
  editor.refresh();
}
function toggleTheme() {
  const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
  if (isDark) {
    document.documentElement.removeAttribute('data-theme');
    localStorage.setItem('pyterm-theme', 'light');
  } else {
    document.documentElement.setAttribute('data-theme', 'dark');
    localStorage.setItem('pyterm-theme', 'dark');
  }
  applyThemeIcon();
}

// ---------- MAXIMIZE ----------
let maximized = null;
function toggleMaximize(which) {
  if (maximized === which) {
    maximized = null;
    splitter.style.display = '';
    editorPanel.style.display = '';
    outputPanelEl.style.display = '';
    setDefaultSizes();
    return;
  }
  maximized = which;
  splitter.style.display = 'none';
  if (which === 'editor') {
    outputPanelEl.style.display = 'none';
    editorPanel.style.display = 'grid';
    editorPanel.style.flex = '1 1 auto';
    editorPanel.style.width = '100%';
    editorPanel.style.height = 'calc(100vh - 180px)';
  } else {
    editorPanel.style.display = 'none';
    outputPanelEl.style.display = 'grid';
    outputPanelEl.style.flex = '1 1 auto';
    outputPanelEl.style.width = '100%';
    outputPanelEl.style.height = 'calc(100vh - 180px)';
  }
  refreshAll();
}

// ---------- AI ----------
async function refreshAiButton() {
  try {
    const r = await fetch('/api/ai/status');
    const data = await r.json();
    hasAiAccess = !!data.has_access;
  } catch (e) { hasAiAccess = false; }
  const btn = document.getElementById('aiToggleBtn');
  if (hasAiAccess) {
    btn.classList.remove('yellow', 'red');
    btn.classList.add('green');
    btn.textContent = '🤖 ✅';
    btn.title = 'AI Generator (akses sudah diberikan)';
  } else {
    btn.classList.remove('green');
    btn.classList.add('yellow');
    btn.textContent = '🤖 🔒';
    btn.title = 'AI Generator (butuh kode akses)';
  }
}

function handleAiButton() {
  if (hasAiAccess) openAiModal('generate');
  else openAiModal('access');
}

const aiModal = document.getElementById('ai-modal');
const aiAccessPanel = document.getElementById('ai-access-panel');
const aiGeneratePanel = document.getElementById('ai-generate-panel');
const aiAccessInput = document.getElementById('ai-access-input');
const aiAccessStatus = document.getElementById('ai-access-status');
const aiPromptInput = document.getElementById('ai-prompt');
const aiModelSelect = document.getElementById('ai-model');
const aiStatusEl = document.getElementById('ai-status');
const aiModalTitle = document.getElementById('ai-modal-title');
const aiModalDesc = document.getElementById('ai-modal-desc');

function openAiModal(mode) {
  aiModal.style.display = 'flex';
  aiAccessStatus.textContent = '';
  aiStatusEl.textContent = '';
  if (mode === 'access') {
    currentAiMode = 'access';
    aiModalTitle.textContent = '🔒 Akses AI - Verifikasi';
    aiModalDesc.textContent = 'Fitur AI butuh kode akses. Tidak punya? Pakai Ctrl+K.';
    aiAccessPanel.style.display = 'block';
    aiGeneratePanel.style.display = 'none';
    aiAccessInput.value = '';
    setTimeout(() => aiAccessInput.focus(), 60);
  } else {
    currentAiMode = mode;
    aiModalTitle.textContent = '🤖 Generate Kode';
    aiModalDesc.textContent = 'Tulis permintaan. Kalau ada kata "tambah", kode akan ditambahkan.';
    aiAccessPanel.style.display = 'none';
    aiGeneratePanel.style.display = 'block';
    aiPromptInput.value = '';
    setTimeout(() => aiPromptInput.focus(), 60);
  }
}
function closeAiModal() { aiModal.style.display = 'none'; }

async function submitAccessCode() {
  const code = aiAccessInput.value.trim();
  if (!code) {
    aiAccessStatus.textContent = '⚠ Masukkan kode akses.';
    aiAccessStatus.style.color = '#FF3B30';
    return;
  }
  aiAccessStatus.textContent = '⏳ Memverifikasi...';
  aiAccessStatus.style.color = '#000';
  try {
    const r = await fetch('/api/ai/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code })
    });
    const data = await r.json();
    if (data.success) {
      hasAiAccess = true;
      await refreshAiButton();
      openAiModal('generate');
    } else {
      aiAccessStatus.textContent = '❌ ' + (data.error || 'Kode salah.');
      aiAccessStatus.style.color = '#FF3B30';
    }
  } catch (e) {
    aiAccessStatus.textContent = '❌ Error: ' + e.message;
    aiAccessStatus.style.color = '#FF3B30';
  }
}

async function generateAiCode() {
  const prompt = aiPromptInput.value.trim();
  const model = aiModelSelect.value;
  if (!prompt) {
    aiStatusEl.textContent = '⚠ Tulis dulu promptnya.';
    aiStatusEl.style.color = '#FF9800';
    return;
  }
  const btn = document.getElementById('ai-generate-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Loading...';
  aiStatusEl.textContent = '⏳ Menghubungi AI...';
  aiStatusEl.style.color = '#000';

  const shouldAppend = /tambah/i.test(prompt);
  const bypass = currentAiMode === 'bypass' ? '1' : '0';

  try {
    const url = `/api/generate?prompt=${encodeURIComponent(prompt)}&model=${encodeURIComponent(model)}&bypass=${bypass}`;
    const r = await fetch(url);
    const data = await r.json();
    if (data.success) {
      if (shouldAppend) {
        const existing = editor.getValue();
        const sep = existing.trim().length ? '\\n\\n' : '';
        editor.setValue(existing + sep + data.code);
      } else {
        editor.setValue(data.code);
      }
      editor.focus();
      closeAiModal();
    } else {
      if (data.need_access) openAiModal('access');
      else {
        aiStatusEl.textContent = '❌ ' + (data.error || 'Gagal.');
        aiStatusEl.style.color = '#FF3B30';
      }
    }
  } catch (e) {
    aiStatusEl.textContent = '❌ Error: ' + e.message;
    aiStatusEl.style.color = '#FF3B30';
  } finally {
    btn.disabled = false;
    btn.textContent = '⚡ Generate';
  }
}

// ---------- MUSIC ----------
const musicModal = document.getElementById('music-modal');
const musicSearchInput = document.getElementById('music-search-input');
const musicResult = document.getElementById('music-result');
const musicResultContent = document.getElementById('music-result-content');
const musicPlayBtn = document.getElementById('music-play-btn');
const musicStatusEl = document.getElementById('music-status');
const musicProgress = document.getElementById('music-progress');
const musicProgressText = document.getElementById('music-progress-text');
const currentMusicBox = document.getElementById('current-music-box');
const currentMusicIcon = document.getElementById('current-music-icon');
const currentMusicText = document.getElementById('current-music-text');
const musicToggleBtn = document.getElementById('musicToggleBtn');

function toggleMusicModal() {
  if (musicModal.style.display === 'flex') closeMusicModal();
  else openMusicModal();
}
function openMusicModal() {
  musicModal.style.display = 'flex';
  updateMusicStatus();
  if (!musicData) setTimeout(() => musicSearchInput.focus(), 60);
}
function closeMusicModal() {
  // Cuma tutup modal, JANGAN stop musik
  musicModal.style.display = 'none';
}

async function searchMusic() {
  const query = musicSearchInput.value.trim();
  if (!query) {
    musicStatusEl.textContent = '⚠ Masukkan judul lagu.';
    musicStatusEl.style.color = '#FF9800';
    return;
  }
  musicStatusEl.textContent = '⏳ Mencari...';
  musicStatusEl.style.color = '#000';
  musicResult.classList.remove('show');
  musicProgress.classList.remove('show');
  musicPlayBtn.disabled = true;
  musicPlayBtn.textContent = '⏳ Menunggu...';

  if (downloadPollTimer) { clearInterval(downloadPollTimer); downloadPollTimer = null; }

  try {
    const r = await fetch('/api/music/search?q=' + encodeURIComponent(query));
    const data = await r.json();
    if (!data.success) {
      musicStatusEl.textContent = '❌ ' + (data.error || 'Tidak ditemukan.');
      musicStatusEl.style.color = '#FF3B30';
      return;
    }
    musicData = data.result;
    musicCacheKey = data.cache_key;

    const thumb = musicData.thumbnail ? `<img src="${musicData.thumbnail}">` : '';
    musicResultContent.innerHTML = `
      ${thumb}
      <div class="track-info">
        <div class="title">${escapeHtml(musicData.title || 'Unknown')}</div>
        <div class="artist">${escapeHtml(musicData.artist || '')}</div>
        <div class="duration">⏱ ${escapeHtml(musicData.duration || 'N/A')} · ${escapeHtml(musicData.album || '')}</div>
      </div>`;
    musicResult.classList.add('show');

    if (data.cached) {
      musicStatusEl.textContent = '✅ Siap diputar (cache).';
      musicStatusEl.style.color = '#00E676';
      musicPlayBtn.disabled = false;
      musicPlayBtn.textContent = '▶ Putar';
      musicProgress.classList.remove('show');
    } else {
      await startDownload(query);
    }
  } catch (e) {
    musicStatusEl.textContent = '❌ Error: ' + e.message;
    musicStatusEl.style.color = '#FF3B30';
  }
}

async function startDownload(query) {
  musicProgress.classList.add('show');
  musicProgressText.textContent = 'Menyiapkan download...';
  musicStatusEl.textContent = '⏳ Download...';
  musicStatusEl.style.color = '#000';
  musicPlayBtn.disabled = true;
  musicPlayBtn.textContent = '⏳ Downloading...';

  try {
    const r = await fetch('/api/music/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query })
    });
    const data = await r.json();
    if (!data.success) {
      musicStatusEl.textContent = '❌ ' + (data.error || 'Gagal download.');
      musicStatusEl.style.color = '#FF3B30';
      musicProgress.classList.remove('show');
      return;
    }
    musicCacheKey = data.cache_key;
    if (data.ready) onDownloadComplete();
    else downloadPollTimer = setInterval(() => pollDownload(), 1000);
  } catch (e) {
    musicStatusEl.textContent = '❌ Error: ' + e.message;
    musicStatusEl.style.color = '#FF3B30';
    musicProgress.classList.remove('show');
  }
}

async function pollDownload() {
  if (!musicCacheKey) return;
  try {
    const r = await fetch('/api/music/check?key=' + encodeURIComponent(musicCacheKey));
    const data = await r.json();
    if (data.ready) {
      clearInterval(downloadPollTimer);
      downloadPollTimer = null;
      onDownloadComplete();
    } else if (data.downloading) {
      musicProgressText.textContent = 'Downloading lagu...';
    }
  } catch (e) {}
}

function onDownloadComplete() {
  musicProgress.classList.remove('show');
  musicStatusEl.textContent = '✅ Download selesai! Siap diputar.';
  musicStatusEl.style.color = '#00E676';
  musicPlayBtn.disabled = false;
  musicPlayBtn.textContent = '▶ Putar';
}

async function playMusic() {
  if (!musicData) {
    musicStatusEl.textContent = '⚠ Cari lagu dulu.';
    musicStatusEl.style.color = '#FF9800';
    return;
  }
  musicPlayBtn.disabled = true;
  musicPlayBtn.textContent = '⏳ Memutar...';
  try {
    const query = musicData.title || '';
    const r = await fetch('/api/music/play', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, title: query })
    });
    const data = await r.json();
    if (data.success) {
      isMusicPlaying = true;
      musicPlayBtn.textContent = '⏸ Sedang Diputar';
      musicPlayBtn.classList.remove('green');
      musicPlayBtn.classList.add('red');
      musicStatusEl.textContent = '✅ ' + (data.message || 'Diputar.');
      musicStatusEl.style.color = '#00E676';
      updateMusicStatus();
    } else {
      if (r.status === 425) {
        musicStatusEl.textContent = '⏳ Masih download...';
        musicStatusEl.style.color = '#FF9800';
        musicProgress.classList.add('show');
        downloadPollTimer = setInterval(() => pollDownload(), 1000);
        musicPlayBtn.textContent = '⏳ Menunggu...';
      } else {
        musicStatusEl.textContent = '❌ ' + (data.error || 'Gagal.');
        musicStatusEl.style.color = '#FF3B30';
        musicPlayBtn.textContent = '▶ Putar';
      }
    }
  } catch (e) {
    musicStatusEl.textContent = '❌ Error: ' + e.message;
    musicStatusEl.style.color = '#FF3B30';
  }
}

async function stopMusic() {
  try {
    await fetch('/api/music/stop');
    isMusicPlaying = false;
    musicPlayBtn.textContent = '▶ Putar';
    musicPlayBtn.classList.remove('red');
    musicPlayBtn.classList.add('green');
    musicStatusEl.textContent = '⏹ Dihentikan.';
    updateMusicStatus();
  } catch (e) {}
}

async function updateMusicStatus() {
  try {
    const r = await fetch('/api/music/status');
    const data = await r.json();
    if (data.playing) {
      currentMusicBox.style.display = 'flex';
      currentMusicIcon.textContent = '🎵';
      currentMusicText.textContent = 'Memutar: ' + (data.data?.title || 'Unknown');
      musicToggleBtn.textContent = '🎵 ▶';
      isMusicPlaying = true;
      musicPlayBtn.textContent = '⏸ Sedang Diputar';
      musicPlayBtn.classList.remove('green');
      musicPlayBtn.classList.add('red');
      musicPlayBtn.disabled = false;
    } else {
      currentMusicBox.style.display = 'flex';
      currentMusicIcon.textContent = '⏸';
      currentMusicText.textContent = 'Tidak ada lagu diputar';
      musicToggleBtn.textContent = '🎵';
      isMusicPlaying = false;
    }
  } catch (e) {}
}

// ---------- LIBRARY ----------
const libraryModal = document.getElementById('library-modal');
const libraryListEl = document.getElementById('library-list');

function openLibrary() {
  libraryModal.style.display = 'flex';
  libraryListEl.innerHTML = '<p class="nb-modal-desc">⏳ Memuat...</p>';
  fetch('/api/files/list').then(r => r.json()).then(data => {
    if (!data.success || !data.files.length) {
      libraryListEl.innerHTML = '<p class="nb-modal-desc">📭 Belum ada file.</p>';
      return;
    }
    libraryListEl.innerHTML = data.files.map(f => `
      <div class="nb-lib-item">
        <div class="nb-lib-item-info">
          <div class="nb-lib-item-name">${escapeHtml(f.name)}</div>
          <div class="nb-lib-item-meta">${f.size} bytes · ${escapeHtml(f.modified)}</div>
        </div>
        <div class="nb-lib-item-actions">
          <button class="nb-btn sm cyan" onclick="openLibraryFile('${escapeHtml(f.name)}')">Buka</button>
          <button class="nb-btn sm yellow" onclick="copyLibraryFile('${escapeHtml(f.name)}')">Salin</button>
          <a class="nb-btn sm pink" href="/api/files/download?name=${encodeURIComponent(f.name)}">Unduh</a>
        </div>
      </div>`).join('');
  }).catch(() => {
    libraryListEl.innerHTML = '<p class="nb-modal-desc">❌ Gagal memuat.</p>';
  });
}
function closeLibrary() { libraryModal.style.display = 'none'; }

function saveCurrentFile() {
  const name = prompt('Simpan sebagai:', currentFileName || 'main.py');
  if (!name) return;
  fetch('/api/files/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, code: editor.getValue() })
  }).then(r => r.json()).then(data => {
    if (data.success) { currentFileName = data.name; alert('✅ Tersimpan: ' + data.name); }
    else alert('❌ ' + (data.error || 'Gagal simpan.'));
  });
}
function openLibraryFile(name) {
  fetch('/api/files/content?name=' + encodeURIComponent(name))
    .then(r => r.json()).then(data => {
      if (data.success) {
        editor.setValue(data.code);
        currentFileName = data.name;
        closeLibrary();
      } else alert('❌ ' + data.error);
    });
}
function copyLibraryFile(name) {
  fetch('/api/files/content?name=' + encodeURIComponent(name))
    .then(r => r.json()).then(data => {
      if (data.success) navigator.clipboard.writeText(data.code).then(() => alert('✅ Disalin!'));
    });
}

// ---------- HELPERS ----------
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

// ---------- SHORTCUTS ----------
window.addEventListener('keydown', (e) => {
  const key = (e.key || '').toLowerCase();
  if ((e.ctrlKey || e.metaKey) && key === 'k') {
    e.preventDefault();
    if (aiModal.style.display === 'flex') closeAiModal();
    else { currentAiMode = 'bypass'; openAiModal('bypass'); }
  }
  if ((e.ctrlKey || e.metaKey) && key === 'e') {
    e.preventDefault();
    toggleMusicModal();
  }
  if (e.key === 'Escape') {
    if (aiModal.style.display === 'flex') closeAiModal();
    if (musicModal.style.display === 'flex') closeMusicModal();
    if (libraryModal.style.display === 'flex') closeLibrary();
  }
});

// ---------- INIT ----------
window.addEventListener('load', () => {
  applyThemeIcon();
  setDefaultSizes();
  refreshAiButton();
  updateMusicStatus();
  setInterval(updateMusicStatus, 3000);
});
</script>
</body>
</html>"""

# ======================================================================
# MAIN
# ======================================================================
if __name__ == "__main__":
    player, _ = get_audio_player()
    print("=" * 60)
    print(f" 🟡 Server     : http://{HOST}:{PORT}")
    print(f" 🎨 Tema       : Neo-Brutalism")
    print(f" 🎵 Audio      : {player if player else '!!! TIDAK ADA (install mpv) !!!'}")
    print(f" 📁 Music cache: {MUSIC_CACHE_DIR}")
    print(f" 🔒 AI Access  : Tombol 🤖 (kode) / Ctrl+K (bypass)")
    print("=" * 60)
    socketio.run(app, host=HOST, port=PORT, debug=False, allow_unsafe_werkzeug=True)