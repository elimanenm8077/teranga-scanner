import os, re, base64, math, zipfile, io, json, hashlib
from pathlib import Path
from flask import Flask, request, jsonify, render_template, redirect, url_for, session
from datetime import datetime, timedelta
from functools import wraps
import sqlite3
import time

from authlib.integrations.flask_client import OAuth

try:
    import rarfile
    RAR_SUPPORT = True
except ImportError:
    RAR_SUPPORT = False

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'teranga-dev-secret-2026-tarkalla')
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

GOOGLE_CLIENT_ID     = os.environ.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
ADMIN_EMAILS         = os.environ.get('ADMIN_EMAILS', '').split(',')

# ================================================================
# DATABASE
# ================================================================

def get_db():
    db = sqlite3.connect('scanner.db')
    db.row_factory = sqlite3.Row
    return db

def init_db():
    with get_db() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT,
            picture TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            last_seen TEXT,
            scan_count INTEGER DEFAULT 0,
            total_time INTEGER DEFAULT 0
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT,
            login_at TEXT DEFAULT (datetime('now')),
            logout_at TEXT,
            duration INTEGER DEFAULT 0,
            scans_in_session INTEGER DEFAULT 0
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS scan_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT,
            scanned_at TEXT DEFAULT (datetime('now')),
            files_count INTEGER,
            threats_found INTEGER,
            critical_count INTEGER
        )''')
        db.commit()

init_db()

# ================================================================
# OAUTH
# ================================================================

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'},
)

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = session.get('user')
        if not user or user.get('email') not in ADMIN_EMAILS:
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated

# ================================================================
# SIGNATURES TERANGA DEV v6.3
# ================================================================

SIGNATURES_LUA = {
    "EXEC CRITIQUE": [
        (r"assert\s*\(\s*load\s*\(d\)\s*\)", 5, "Pattern exact assert(load(d))"),
        (r"pcall\s*\(\s*function\s*\(\s*\)\s*assert\s*\(\s*load", 5, "Pattern exact pcall+assert+load"),
        (r"loadstring\s*\(\s*[^)]*https?://", 5, "Code charge depuis URL"),
        (r"LoadResourceFile\s*\([^)]+\)\s*:sub\s*\(\s*8[0-9]{4}", 5, "Fake Font Payload - offset binaire"),
        (r"LoadResourceFile\s*\([^)]*\.ttf[^)]*\)", 5, "Chargement police .ttf suspect"),
        (r"_G\s*\[\s*string\.char\s*\(", 5, "Relais load() via string.char"),
        (r"string\.char\s*\(\s*108\s*,\s*111\s*,\s*97\s*,\s*100\s*\)", 5, "load() encode ASCII"),
        (r"loadFonts\s*\(", 5, "Appel loadFonts backdoor"),
        (r"\):sub\s*\(\s*87565\s*\)", 5, "Fake Font offset 87565"),
        (r"\):sub\s*\(\s*87566\s*\)", 5, "Fake Font offset 87566"),
    ],
    "DOMAINES CONNUS": [
        (r"cipher-panel", 5, "Backdoor Cipher Panel"),
        (r"ciphercorp\.net", 5, "CipherCorp connu"),
        (r"ciphercorp", 5, "Panel backdoor CipherCorp"),
        (r"SEIZED YOUR SERVER", 5, "Message grief CipherCorp"),
        (r"NoctuaPanel", 5, "Panel NoctuaPanel"),
        (r"blum-panel", 5, "Backdoor Blum Panel"),
        (r"BLUM OWNS THIS SERVER", 5, "Message grief Blum"),
        (r"NO ESCAPE NO MERCY", 5, "Message grief Blum"),
        (r"gfxpanel\.org", 5, "Panel backdoor GFX"),
        (r"KISSED UR SERVER", 5, "Message grief GFX Panel"),
        (r"GFXPANEL", 5, "Panel backdoor GFX"),
        (r"kvac\.cz", 5, "Panel backdoor KVAC"),
        (r"kvacdoor", 5, "KVacDoor backdoor"),
        (r"KVacDoor", 5, "KVacDoor backdoor"),
        (r"w88C8A7A5BE032EABa", 5, "ID session KVAC Panel"),
        (r"tema-ninja\.com", 5, "Domaine malveillant connu"),
        (r"ketamin\.cc", 5, "Domaine malveillant connu"),
        (r"FiveMStatusCheck", 5, "Backdoor FiveMStatusCheck"),
        (r"hlcm5alcv13", 5, "Domaine backdoor hlcm5alcv13"),
        (r"pastebin\.com/raw", 4, "Code charge depuis Pastebin"),
        (r"helpCode", 5, "Signature helpCode backdoor"),
        (r"PhpbjiNCvZAbxm", 5, "Event Cipher identifie"),
        (r"Enchanced_Tabs", 5, "Variable Cipher Enchanced_Tabs"),
        (r"helperServer", 5, "Variable Cipher helperServer"),
        (r"BlevEWEgOIJqpxywKOpTWoQFlyZZPwiTyoVjZjINEOavZjHvgEKnyNiPuHCilyVpgfpolZ", 5, "Variable auto-reinject Cipher"),
        (r"discord\.gg/[Cc]ipher[Cc]orp", 5, "Discord CipherCorp connu"),
        (r"discord\.gg/ycNEVGnu", 5, "Discord Blum Panel connu"),
    ],
    "OBFUSCATION": [
        (r"_G\s*\[", 3, "Acces table globale _G"),
        (r"string\.char\s*\([0-9,\s]{10,}\)", 4, "Chaine ASCII encodee"),
        (r"local\s+random_char\s*=", 5, "Variable random_char (Cipher)"),
        (r"function\s+str_utf8\s*\(", 5, "Fonction str_utf8 (Cipher)"),
        (r"bit\.bxor\s*\(", 4, "XOR decode - Blum Panel"),
    ],
    "PRIVILEGES": [
        (r"ExecuteCommand\s*\(['\"]add_ace", 5, "Escalade ACE privilege"),
        (r"ExecuteCommand\s*\(['\"]add_principal", 5, "Ajout principal suspect"),
    ],
    "EXFILTRATION": [
        (r"GetConvar\s*\(['\"]rcon_password", 5, "Vol rcon_password"),
        (r"GetConvar\s*\(['\"]steam_webApiKey", 5, "Vol Steam API key"),
        (r"GetConvar\s*\(['\"]mysql_connection_string", 5, "Vol credentials MySQL"),
    ],
    "RESEAU": [
        (r"PerformHttpRequestInternalEx", 5, "HTTP Request interne cache"),
        (r"__cfx_internal:httpResponse", 5, "Event interne CFX suspect"),
    ],
}

SIGNATURES_NUI = {
    "NUI": [
        (r"eval\s*\(\s*atob\s*\(", 5, "eval(atob()) - payload crypte"),
        (r"\(\s*\)\s*=>\s*eval\s*\(d\s*\)", 5, "Arrow eval(d) - Fake Font JS"),
        (r"gfxpanel", 5, "GFX Panel NUI"),
        (r"KISSED UR SERVER", 5, "GFX Panel message NUI"),
        (r"top\.citFrames\s*\[", 4, "Acces frame NUI cross-resource"),
    ]
}

SIGNATURES_CFG = {
    "CFG": [
        (r"exec\s+https?://", 5, "Config distante"),
    ]
}

WHITELIST_PATTERNS = [
    r"discord\.com/api/webhooks.*SendWebhookMessage",
    r"PerformHttpRequest.*version.*check",
    r"local\s+\w+\s*=\s*LoadResourceFile",
    r"if\s+not\s+IsDuplicityVersion\s*\(\s*\)",
    r"_G\s*\[bridgeKey\]",
    r"_G\s*\[k\]",
    r"L\d+_\d+\s*=",
    r"Protected by CheapM",
    r"SaveResourceFile.*config",
    r"SaveResourceFile.*settings",
    r"SaveResourceFile.*zones",
    r"SaveResourceFile.*items",
    r"SaveResourceFile.*transcript",
    r"SaveResourceFile.*log",
    r"SaveResourceFile.*fix\.sql",
    r"SaveResourceFile.*defaultdb",
    r"os\.execute\s*\(\s*['\"]mkdir",
    r"GetConvar\s*\(['\"]rcon_password.*==",
    r"discord\.com/api/webhooks.*webhook\s*=",
    r"discord\.com/api/webhooks.*SendBill",
    r"discord\.com/api/webhooks.*RefundBill",
    r"discord\.com/api/webhooks.*BIRDY_WEBHOOK",
    r"discord\.com/api/webhooks.*INSTAPIC_WEBHOOK",
    r"discord\.com/api/webhooks.*give_vehicle",
    r"discord\.com/api/webhooks.*DiscordAnnounce",
    r"discord\.com/api/webhooks.*DiscordWebhook\s*=",
]

SKIP_EXTENSIONS = {'.png','.jpg','.jpeg','.gif','.webp','.ico','.mp3','.mp4','.ogg','.wav','.db','.sql','.md','.txt','.xml','.gitignore','.bat','.sh','.exe','.dll','.so','.pak','.ytd','.ydr','.yft','.ybn','.ymap','.ytyp','.ymt'}
SCAN_EXTENSIONS  = {'.lua','.js','.cfg','.html','.htm','.ttf','.otf','.woff','.json'}
SKIP_DIRS        = {'node_modules','dist','.git','__pycache__','vendor','.svn'}

# ================================================================
# SCANNER ENGINE
# ================================================================

def is_whitelisted(context):
    return any(re.search(p, context, re.I) for p in WHITELIST_PATTERNS)

def entropy(s):
    if len(s) < 50: return 0.0
    e = 0.0
    for x in range(256):
        p = s.count(chr(x)) / len(s)
        if p > 0: e -= p * math.log2(p)
    return e

def check_base64(content):
    findings = []
    for m in re.findall(r'[A-Za-z0-9+/]{60,}={0,2}', content):
        try:
            d = base64.b64decode(m).decode('utf-8', errors='ignore')
            keywords = ['loadstring','PerformHttpRequest','ExecuteCommand','add_ace','gfxpanel','kvac','cipher','blum','bxor','ketamin','tema-ninja']
            if any(k in d for k in keywords):
                findings.append({"line": "b64", "category": "BASE64", "pattern": m[:60], "severity": 4, "description": f"Payload base64 => {d[:80]}"})
        except: pass
    return findings

def scan_file(filename, content_bytes):
    try:
        content = content_bytes.decode('utf-8', errors='ignore')
    except:
        return {"file": filename, "findings": [], "score": 0, "risk_level": "CLEAN"}

    if len(content) < 30:
        return {"file": filename, "findings": [], "score": 0, "risk_level": "CLEAN"}

    ext  = Path(filename).suffix.lower()
    name = Path(filename).name.lower()

    if name == 'fxmanifest.lua' or ext == '.cfg':
        sigs = SIGNATURES_CFG
    elif ext in ('.html', '.htm'):
        sigs = SIGNATURES_NUI
    else:
        sigs = SIGNATURES_LUA

    lines = content.split('\n')
    findings = []
    score    = 0

    for cat, patterns in sigs.items():
        for pat, sev, desc in patterns:
            for m in re.finditer(pat, content, re.I | re.M):
                ln = content[:m.start()].count('\n') + 1
                ctx_start = max(0, ln - 4)
                ctx_end   = min(len(lines), ln + 4)
                ctx = '\n'.join(lines[ctx_start:ctx_end]).lower()
                if is_whitelisted(ctx):
                    continue
                score += sev
                findings.append({
                    "line": ln, "category": cat,
                    "pattern": m.group()[:150], "severity": sev,
                    "description": desc,
                    "context": '\n'.join(
                        f"{'>>>' if i==ln-1 else '   '} {i+1:4d} | {lines[i].rstrip()}"
                        for i in range(ctx_start, ctx_end)
                    )
                })

    for f in check_base64(content):
        score += f['severity']
        findings.append(f)

    ent = entropy(content[:2000])
    if ent > 6.2 and any('EXEC' in f['category'] or 'OBFUSC' in f['category'] for f in findings):
        score += 2
        findings.append({"line": "ent", "category": "ENTROPIE", "pattern": f"{ent:.2f}/8.0", "severity": 2, "description": "Obfuscation lourde detectee", "context": ""})

    risk = "CLEAN" if score==0 else "LOW" if score<=3 else "MEDIUM" if score<=7 else "CRITICAL"
    return {"file": filename, "score": score, "risk_level": risk, "findings": findings, "total_findings": len(findings)}

def should_scan(filename):
    ext   = Path(filename).suffix.lower()
    parts = Path(filename).parts
    for part in parts:
        if part in SKIP_DIRS: return False
    if ext in SKIP_EXTENSIONS: return False
    if ext not in SCAN_EXTENSIONS and ext != '': return False
    return True

def extract_and_scan(file_storage):
    filename = file_storage.filename
    content  = file_storage.read()
    ext      = Path(filename).suffix.lower()
    results  = []

    if ext == '.zip':
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                for name in zf.namelist():
                    if should_scan(name) and not name.endswith('/'):
                        try:
                            data = zf.read(name)
                            if len(data) < 2_000_000:
                                r = scan_file(name, data)
                                if r.get('findings') or r.get('score', 0) > 0:
                                    results.append(r)
                        except: pass
        except Exception as e:
            results.append({"file": filename, "error": str(e), "findings": [], "score": 0, "risk_level": "ERREUR"})
    elif ext == '.rar' and RAR_SUPPORT:
        try:
            rf = rarfile.RarFile(io.BytesIO(content))
            for name in rf.namelist():
                if should_scan(name) and not name.endswith('/'):
                    try:
                        data = rf.read(name)
                        if len(data) < 2_000_000:
                            r = scan_file(name, data)
                            if r.get('findings') or r.get('score', 0) > 0:
                                results.append(r)
                    except: pass
        except Exception as e:
            results.append({"file": filename, "error": str(e), "findings": [], "score": 0, "risk_level": "ERREUR"})
    else:
        if should_scan(filename):
            r = scan_file(filename, content)
            if r.get('findings') or r.get('score', 0) > 0:
                results.append(r)

    return results

# ================================================================
# ROUTES AUTH
# ================================================================

@app.route('/login')
def login():
    redirect_uri = url_for('callback', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/callback')
def callback():
    try:
        token = google.authorize_access_token()
        user_info = token.get('userinfo')
        if not user_info:
            import httpx
            resp = httpx.get('https://www.googleapis.com/oauth2/v3/userinfo',
                             headers={'Authorization': f'Bearer {token["access_token"]}'})
            user_info = resp.json()

        email   = user_info.get('email')
        name    = user_info.get('name', email)
        picture = user_info.get('picture', '')

        with get_db() as db:
            db.execute('''INSERT INTO users (email, name, picture, last_seen)
                          VALUES (?, ?, ?, datetime('now'))
                          ON CONFLICT(email) DO UPDATE SET
                          name=excluded.name, picture=excluded.picture,
                          last_seen=datetime('now')''', (email, name, picture))
            db.execute('''INSERT INTO sessions (user_email, login_at)
                          VALUES (?, datetime('now'))''', (email,))
            db.commit()

        session['user'] = {'email': email, 'name': name, 'picture': picture}
        session['session_start'] = int(time.time())
        session['session_scans'] = 0

        return redirect(url_for('index'))
    except Exception as e:
        return redirect(url_for('login'))

@app.route('/logout')
def logout():
    user = session.get('user')
    if user:
        start    = session.get('session_start', int(time.time()))
        duration = int(time.time()) - start
        scans    = session.get('session_scans', 0)
        with get_db() as db:
            db.execute('''UPDATE sessions SET logout_at=datetime('now'),
                          duration=?, scans_in_session=?
                          WHERE user_email=? AND logout_at IS NULL''',
                       (duration, scans, user['email']))
            db.execute('''UPDATE users SET total_time=total_time+?
                          WHERE email=?''', (duration, user['email']))
            db.commit()
    session.clear()
    return redirect(url_for('login'))

# ================================================================
# ROUTES PRINCIPALES
# ================================================================

@app.route('/')
@login_required
def index():
    user = session.get('user')
    is_admin = user and user.get('email') in ADMIN_EMAILS
    return render_template('index.html', rar_support=RAR_SUPPORT, user=user, is_admin=is_admin)

@app.route('/scan', methods=['POST'])
@login_required
def scan():
    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': 'Aucun fichier recu'}), 400

    all_results = []
    for f in files:
        if not f.filename: continue
        results = extract_and_scan(f)
        all_results.extend(results)

    threats  = [r for r in all_results if r.get('findings')]
    critical = sum(1 for r in all_results if r.get('risk_level') == 'CRITICAL')
    medium   = sum(1 for r in all_results if r.get('risk_level') == 'MEDIUM')
    low      = sum(1 for r in all_results if r.get('risk_level') == 'LOW')

    user = session.get('user')
    if user:
        session['session_scans'] = session.get('session_scans', 0) + 1
        with get_db() as db:
            db.execute('UPDATE users SET scan_count=scan_count+1 WHERE email=?', (user['email'],))
            db.execute('''INSERT INTO scan_logs (user_email, files_count, threats_found, critical_count)
                          VALUES (?, ?, ?, ?)''', (user['email'], len(all_results), len(threats), critical))
            db.commit()

    return jsonify({
        'results': all_results,
        'stats': {
            'scanned': len(all_results),
            'threats': len(threats),
            'critical': critical,
            'medium':   medium,
            'low':      low,
            'clean':    len(all_results) - len(threats)
        },
        'date': datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    })

# ================================================================
# ADMIN DASHBOARD
# ================================================================

@app.route('/admin')
@login_required
@admin_required
def admin():
    with get_db() as db:
        users = db.execute('''
            SELECT u.*, COUNT(s.id) as session_count,
                   SUM(s.scans_in_session) as total_scans_sessions
            FROM users u
            LEFT JOIN sessions s ON s.user_email = u.email
            GROUP BY u.email
            ORDER BY u.last_seen DESC
        ''').fetchall()

        sessions_list = db.execute('''
            SELECT s.*, u.name
            FROM sessions s
            LEFT JOIN users u ON u.email = s.user_email
            ORDER BY s.login_at DESC
            LIMIT 50
        ''').fetchall()

        scan_logs = db.execute('''
            SELECT sl.*, u.name
            FROM scan_logs sl
            LEFT JOIN users u ON u.email = sl.user_email
            ORDER BY sl.scanned_at DESC
            LIMIT 50
        ''').fetchall()

        total_users  = db.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        total_scans  = db.execute('SELECT SUM(scan_count) FROM users').fetchone()[0] or 0
        total_threats = db.execute('SELECT SUM(threats_found) FROM scan_logs').fetchone()[0] or 0

    return render_template('admin.html',
        users=users,
        sessions_list=sessions_list,
        scan_logs=scan_logs,
        total_users=total_users,
        total_scans=total_scans,
        total_threats=total_threats,
        admin_user=session.get('user')
    )

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'version': '6.3', 'rar_support': RAR_SUPPORT})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
