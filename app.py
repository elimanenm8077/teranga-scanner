import os, re, base64, math, zipfile, io, json, hashlib, urllib.parse
from pathlib import Path
from flask import Flask, request, jsonify, render_template, redirect, url_for, session
from datetime import datetime
from functools import wraps
import time
import requests

try:
    import psycopg2
    import psycopg2.extras
    POSTGRES = True
except ImportError:
    import sqlite3
    POSTGRES = False

try:
    import rarfile
    RAR_SUPPORT = True
except ImportError:
    RAR_SUPPORT = False

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'teranga-dev-secret-2026-tarkalla')
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

CLIENT_ID         = "983810213589-ia9cukopmegpvt9jfj0s32e8bsl93s0s.apps.googleusercontent.com"
CLIENT_SECRET     = "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"
REDIRECT_URI      = "https://teranga-scanner.onrender.com/callback"
ADMIN_EMAILS      = [e.strip() for e in os.environ.get('ADMIN_EMAILS', 'elimanenm8077@gmail.com').split(',')]
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
GOOGLE_AUTH_URL   = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL  = "https://oauth2.googleapis.com/token"
GOOGLE_USER_URL   = "https://www.googleapis.com/oauth2/v2/userinfo"
DATABASE_URL      = os.environ.get('DATABASE_URL', '')

# ================================================================
# DATABASE
# ================================================================

def get_db():
    if POSTGRES and DATABASE_URL:
        return psycopg2.connect(DATABASE_URL)
    else:
        import sqlite3
        conn = sqlite3.connect('/tmp/scanner.db')
        conn.row_factory = sqlite3.Row
        return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    if POSTGRES and DATABASE_URL:
        cur.execute('''CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY, email TEXT UNIQUE NOT NULL,
            name TEXT, picture TEXT, created_at TIMESTAMP DEFAULT NOW(),
            last_seen TIMESTAMP, scan_count INTEGER DEFAULT 0,
            total_time INTEGER DEFAULT 0, banned INTEGER DEFAULT 0
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS sessions (
            id SERIAL PRIMARY KEY, user_email TEXT,
            login_at TIMESTAMP DEFAULT NOW(), logout_at TIMESTAMP,
            duration INTEGER DEFAULT 0, scans_in_session INTEGER DEFAULT 0
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS scan_logs (
            id SERIAL PRIMARY KEY, user_email TEXT,
            scanned_at TIMESTAMP DEFAULT NOW(), files_count INTEGER,
            threats_found INTEGER, critical_count INTEGER
        )''')
    else:
        cur.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL,
            name TEXT, picture TEXT, created_at TEXT DEFAULT (datetime('now')),
            last_seen TEXT, scan_count INTEGER DEFAULT 0,
            total_time INTEGER DEFAULT 0, banned INTEGER DEFAULT 0
        )''')
        try: cur.execute('ALTER TABLE users ADD COLUMN banned INTEGER DEFAULT 0')
        except: pass
        cur.execute('''CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_email TEXT,
            login_at TEXT DEFAULT (datetime('now')), logout_at TEXT,
            duration INTEGER DEFAULT 0, scans_in_session INTEGER DEFAULT 0
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS scan_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_email TEXT,
            scanned_at TEXT DEFAULT (datetime('now')), files_count INTEGER,
            threats_found INTEGER, critical_count INTEGER
        )''')
    conn.commit(); cur.close(); conn.close()

init_db()

def db_execute(query, params=(), fetchone=False, fetchall=False):
    conn = get_db()
    if POSTGRES and DATABASE_URL:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        query = query.replace('?', '%s').replace("datetime('now')", "NOW()")
    else:
        cur = conn.cursor()
    try:
        cur.execute(query, params)
        result = None
        if fetchone:
            row = cur.fetchone()
            result = dict(row) if (row and POSTGRES and DATABASE_URL) else row
        elif fetchall:
            rows = cur.fetchall()
            result = [dict(r) for r in rows] if (rows and POSTGRES and DATABASE_URL) else rows
        conn.commit()
        return result
    except Exception as e:
        conn.rollback(); raise e
    finally:
        cur.close(); conn.close()

# ================================================================
# AUTH
# ================================================================

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user'):
            return redirect(url_for('login_page'))
        email = session['user'].get('email')
        try:
            ph = '%s' if (POSTGRES and DATABASE_URL) else '?'
            u = db_execute(f'SELECT banned FROM users WHERE email={ph}', (email,), fetchone=True)
            if u and (u['banned'] if isinstance(u, dict) else u[0]):
                session.clear()
                return redirect(url_for('login_page'))
        except: pass
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

@app.route('/login')
def login_page():
    if session.get('user'):
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/google_login')
def google_login():
    params = {'client_id': CLIENT_ID, 'redirect_uri': REDIRECT_URI,
              'response_type': 'code', 'scope': 'openid email profile', 'access_type': 'offline'}
    return redirect(GOOGLE_AUTH_URL + '?' + urllib.parse.urlencode(params))

@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code: return redirect(url_for('login_page'))
    try:
        token_resp = requests.post(GOOGLE_TOKEN_URL, data={
            'code': code, 'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET,
            'redirect_uri': REDIRECT_URI, 'grant_type': 'authorization_code'})
        token_data = token_resp.json()
        access_token = token_data.get('access_token')
        if not access_token: return redirect(url_for('login_page'))
        user_resp = requests.get(GOOGLE_USER_URL, headers={'Authorization': f'Bearer {access_token}'})
        user_info = user_resp.json()
        email = user_info.get('email', '')
        name = user_info.get('name', email)
        picture = user_info.get('picture', '')
        ph = '%s' if (POSTGRES and DATABASE_URL) else '?'
        existing = db_execute(f'SELECT banned FROM users WHERE email={ph}', (email,), fetchone=True)
        if existing and (existing['banned'] if isinstance(existing, dict) else existing[0]):
            return render_template('login.html', error="Votre compte a été banni.")
        if POSTGRES and DATABASE_URL:
            db_execute('''INSERT INTO users (email, name, picture, last_seen) VALUES (%s,%s,%s,NOW())
                          ON CONFLICT (email) DO UPDATE SET name=EXCLUDED.name, picture=EXCLUDED.picture, last_seen=NOW()''',
                       (email, name, picture))
        else:
            db_execute('''INSERT INTO users (email, name, picture, last_seen) VALUES (?,?,?,datetime('now'))
                          ON CONFLICT(email) DO UPDATE SET name=excluded.name, picture=excluded.picture, last_seen=datetime('now')''',
                       (email, name, picture))
        db_execute(f'INSERT INTO sessions (user_email) VALUES ({ph})', (email,))
        session['user'] = {'email': email, 'name': name, 'picture': picture}
        session['session_start'] = int(time.time())
        session['session_scans'] = 0
        return redirect(url_for('index'))
    except: return redirect(url_for('login_page'))

@app.route('/logout')
def logout():
    user = session.get('user')
    if user:
        start = session.get('session_start', int(time.time()))
        duration = int(time.time()) - start
        scans = session.get('session_scans', 0)
        ph = '%s' if (POSTGRES and DATABASE_URL) else '?'
        now = 'NOW()' if (POSTGRES and DATABASE_URL) else "datetime('now')"
        try:
            db_execute(f"UPDATE sessions SET logout_at={now}, duration={ph}, scans_in_session={ph} WHERE user_email={ph} AND logout_at IS NULL",
                       (duration, scans, user['email']))
            db_execute(f'UPDATE users SET total_time=total_time+{ph} WHERE email={ph}', (duration, user['email']))
        except: pass
    session.clear()
    return redirect(url_for('login_page'))

# ================================================================
# SIGNATURES v6.3
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

SIGNATURES_CFG = {"CFG": [(r"exec\s+https?://", 5, "Config distante")]}

WHITELIST_PATTERNS = [
    r"discord\.com/api/webhooks.*SendWebhookMessage",
    r"PerformHttpRequest.*version.*check",
    r"local\s+\w+\s*=\s*LoadResourceFile",
    r"if\s+not\s+IsDuplicityVersion\s*\(\s*\)",
    r"_G\s*\[bridgeKey\]", r"_G\s*\[k\]", r"L\d+_\d+\s*=",
    r"Protected by CheapM",
    r"SaveResourceFile.*config", r"SaveResourceFile.*settings",
    r"SaveResourceFile.*zones", r"SaveResourceFile.*items",
    r"SaveResourceFile.*transcript", r"SaveResourceFile.*log",
    r"SaveResourceFile.*fix\.sql", r"SaveResourceFile.*defaultdb",
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
    try: content = content_bytes.decode('utf-8', errors='ignore')
    except: return {"file": filename, "findings": [], "score": 0, "risk_level": "CLEAN"}
    if len(content) < 30: return {"file": filename, "findings": [], "score": 0, "risk_level": "CLEAN"}
    ext = Path(filename).suffix.lower()
    name = Path(filename).name.lower()
    if name == 'fxmanifest.lua' or ext == '.cfg': sigs = SIGNATURES_CFG
    elif ext in ('.html', '.htm'): sigs = SIGNATURES_NUI
    else: sigs = SIGNATURES_LUA
    lines = content.split('\n')
    findings = []; score = 0
    for cat, patterns in sigs.items():
        for pat, sev, desc in patterns:
            for m in re.finditer(pat, content, re.I | re.M):
                ln = content[:m.start()].count('\n') + 1
                ctx_start = max(0, ln - 4); ctx_end = min(len(lines), ln + 4)
                ctx = '\n'.join(lines[ctx_start:ctx_end]).lower()
                if is_whitelisted(ctx): continue
                score += sev
                findings.append({"line": ln, "category": cat, "pattern": m.group()[:150], "severity": sev, "description": desc,
                    "context": '\n'.join(f"{'>>>' if i==ln-1 else '   '} {i+1:4d} | {lines[i].rstrip()}" for i in range(ctx_start, ctx_end))})
    for f in check_base64(content): score += f['severity']; findings.append(f)
    ent = entropy(content[:2000])
    if ent > 6.2 and any('EXEC' in f['category'] for f in findings):
        score += 2; findings.append({"line": "ent", "category": "ENTROPIE", "pattern": f"{ent:.2f}/8.0", "severity": 2, "description": "Obfuscation lourde", "context": ""})
    risk = "CLEAN" if score==0 else "LOW" if score<=3 else "MEDIUM" if score<=7 else "CRITICAL"
    return {"file": filename, "score": score, "risk_level": risk, "findings": findings, "total_findings": len(findings), "content": content}

def should_scan(filename):
    ext = Path(filename).suffix.lower()
    for part in Path(filename).parts:
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
                                if r.get('findings') or r.get('score', 0) > 0: results.append(r)
                        except: pass
        except Exception as e: results.append({"file": filename, "error": str(e), "findings": [], "score": 0, "risk_level": "ERREUR"})
    elif ext == '.rar' and RAR_SUPPORT:
        try:
            rf = rarfile.RarFile(io.BytesIO(content))
            for name in rf.namelist():
                if should_scan(name) and not name.endswith('/'):
                    try:
                        data = rf.read(name)
                        if len(data) < 2_000_000:
                            r = scan_file(name, data)
                            if r.get('findings') or r.get('score', 0) > 0: results.append(r)
                    except: pass
        except Exception as e: results.append({"file": filename, "error": str(e), "findings": [], "score": 0, "risk_level": "ERREUR"})
    else:
        if should_scan(filename):
            r = scan_file(filename, content)
            if r.get('findings') or r.get('score', 0) > 0: results.append(r)
    return results

# ================================================================
# AGENT IA CLAUDE
# ================================================================

@app.route('/ai_analyze', methods=['POST'])
@login_required
def ai_analyze():
    if not ANTHROPIC_API_KEY:
        return jsonify({'error': 'API Claude non configurée'}), 500

    data = request.get_json()
    filename = data.get('filename', '')
    content  = data.get('content', '')
    findings = data.get('findings', [])

    if not content:
        return jsonify({'error': 'Contenu manquant'}), 400

    # Extrait ciblé autour des lignes détectées
    lines = content.split('\n')
    target_lines = set()
    for f in findings[:10]:
        if isinstance(f.get('line'), int):
            for i in range(max(0, f['line']-10), min(len(lines), f['line']+10)):
                target_lines.add(i)

    if target_lines:
        excerpts = []
        for i in sorted(target_lines):
            excerpts.append(f"{i+1:4d} | {lines[i]}")
        content_preview = '\n'.join(excerpts)
    else:
        content_preview = content[:6000] if len(content) > 6000 else content

    findings_text = '\n'.join([
        f"- Ligne {f.get('line')}: [{f.get('category')}] {f.get('description')} — `{f.get('pattern','')[:80]}`"
        for f in findings[:10]
    ])

    prompt = f"""Tu es un expert en sécurité FiveM spécialisé dans la détection de backdoors Lua.

Fichier analysé : `{filename}`

Détections confirmées par le scanner :
{findings_text}

Extrait du fichier autour des lignes détectées :
```lua
{content_preview}
```

Ta mission :
1. Explique en français ce que font exactement les backdoors détectés (2-3 phrases max par backdoor)
2. Donne le numéro exact de chaque ligne à supprimer avec le contenu exact de la ligne
3. Explique pourquoi chaque ligne doit être supprimée
IMPORTANT: Ces détections ont été confirmées par le scanner Teranga DEV avec des signatures précises et vérifiées. Fais confiance aux détections. Ne dis JAMAIS "faux positif" — ces signatures sont exactes et connues.

Réponds UNIQUEMENT en JSON avec ce format :
{{
  "analyse": "explication claire des backdoors trouvés",
  "lignes_supprimees": [
    {{"ligne": 430, "contenu": "code exact de la ligne", "raison": "explication"}}
  ]
}}"""

    try:
        resp = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json'
            },
            json={
                'model': 'claude-sonnet-4-6',
                'max_tokens': 4000,
                'messages': [{'role': 'user', 'content': prompt}]
            },
            timeout=60
        )
        resp_data = resp.json()
        if 'error' in resp_data:
            return jsonify({'error': resp_data['error'].get('message', 'Erreur API Claude')}), 500
        if 'content' not in resp_data:
            return jsonify({'error': f'Réponse inattendue: {str(resp_data)[:200]}'}), 500
        text = resp_data['content'][0]['text']
        try:
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            result = json.loads(json_match.group()) if json_match else {'analyse': text, 'lignes_supprimees': []}
        except:
            result = {'analyse': text, 'lignes_supprimees': []}
        return jsonify({'success': True, 'result': result, 'filename': filename})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ================================================================
# ROUTES PRINCIPALES
# ================================================================

@app.route('/')
@login_required
def index():
    user = session.get('user')
    is_admin = user and user.get('email') in ADMIN_EMAILS
    ai_enabled = bool(ANTHROPIC_API_KEY)
    return render_template('index.html', rar_support=RAR_SUPPORT, user=user, is_admin=is_admin, ai_enabled=ai_enabled)

@app.route('/scan', methods=['POST'])
@login_required
def scan():
    files = request.files.getlist('files')
    if not files: return jsonify({'error': 'Aucun fichier recu'}), 400
    all_results = []
    for f in files:
        if not f.filename: continue
        all_results.extend(extract_and_scan(f))
    for r in all_results:
        r.pop('content', None)
    threats  = [r for r in all_results if r.get('findings')]
    critical = sum(1 for r in all_results if r.get('risk_level') == 'CRITICAL')
    medium   = sum(1 for r in all_results if r.get('risk_level') == 'MEDIUM')
    low      = sum(1 for r in all_results if r.get('risk_level') == 'LOW')
    user = session.get('user')
    if user:
        session['session_scans'] = session.get('session_scans', 0) + 1
        ph = '%s' if (POSTGRES and DATABASE_URL) else '?'
        try:
            db_execute(f'UPDATE users SET scan_count=scan_count+1 WHERE email={ph}', (user['email'],))
            db_execute(f'INSERT INTO scan_logs (user_email, files_count, threats_found, critical_count) VALUES ({ph},{ph},{ph},{ph})',
                       (user['email'], len(all_results), len(threats), critical))
        except: pass
    return jsonify({
        'results': all_results,
        'stats': {'scanned': len(all_results), 'threats': len(threats), 'critical': critical, 'medium': medium, 'low': low, 'clean': len(all_results)-len(threats)},
        'date': datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    })

# ================================================================
# ADMIN
# ================================================================

def fmt_date(d):
    if not d: return '—'
    try: return str(d)[:16]
    except: return '—'

@app.route('/admin')
@login_required
@admin_required
def admin():
    try:
        users_raw    = db_execute('SELECT u.*, COUNT(s.id) as session_count FROM users u LEFT JOIN sessions s ON s.user_email=u.email GROUP BY u.id, u.email, u.name, u.picture, u.created_at, u.last_seen, u.scan_count, u.total_time, u.banned ORDER BY u.last_seen DESC NULLS LAST', fetchall=True)
        sessions_raw = db_execute('SELECT s.*, u.name FROM sessions s LEFT JOIN users u ON u.email=s.user_email ORDER BY s.login_at DESC LIMIT 50', fetchall=True)
        logs_raw     = db_execute('SELECT sl.*, u.name FROM scan_logs sl LEFT JOIN users u ON u.email=sl.user_email ORDER BY sl.scanned_at DESC LIMIT 50', fetchall=True)
        total_users   = db_execute('SELECT COUNT(*) as c FROM users', fetchone=True)
        total_scans   = db_execute('SELECT SUM(scan_count) as s FROM users', fetchone=True)
        total_threats = db_execute('SELECT SUM(threats_found) as t FROM scan_logs', fetchone=True)
        def val(r, k): return (r.get(k) if isinstance(r, dict) else r[0]) or 0 if r else 0
        users = []
        for u in (users_raw or []):
            row = dict(u)
            row['created_at_fmt'] = fmt_date(row.get('created_at'))
            row['last_seen_fmt']  = fmt_date(row.get('last_seen'))
            users.append(row)
        sessions_list = []
        for s in (sessions_raw or []):
            row = dict(s)
            row['login_at_fmt']  = fmt_date(row.get('login_at'))
            row['logout_at_fmt'] = fmt_date(row.get('logout_at'))
            sessions_list.append(row)
        scan_logs = []
        for sl in (logs_raw or []):
            row = dict(sl)
            row['scanned_at_fmt'] = fmt_date(row.get('scanned_at'))
            scan_logs.append(row)
    except Exception as e:
        users = []; sessions_list = []; scan_logs = []
        total_users = total_scans = total_threats = 0
        def val(r, k): return 0
    return render_template('admin.html',
        users=users, sessions_list=sessions_list, scan_logs=scan_logs,
        total_users=val(total_users, 'c'), total_scans=val(total_scans, 's'),
        total_threats=val(total_threats, 't'),
        admin_user=session.get('user'), admin_emails=ADMIN_EMAILS)

@app.route('/admin/ban', methods=['POST'])
@login_required
@admin_required
def ban_user():
    data = request.get_json()
    email = data.get('email')
    if not email or email in ADMIN_EMAILS: return jsonify({'success': False, 'error': 'Action non autorisee'})
    ph = '%s' if (POSTGRES and DATABASE_URL) else '?'
    db_execute(f'UPDATE users SET banned=1 WHERE email={ph}', (email,))
    return jsonify({'success': True})

@app.route('/admin/unban', methods=['POST'])
@login_required
@admin_required
def unban_user():
    data = request.get_json()
    email = data.get('email')
    if not email: return jsonify({'success': False, 'error': 'Email manquant'})
    ph = '%s' if (POSTGRES and DATABASE_URL) else '?'
    db_execute(f'UPDATE users SET banned=0 WHERE email={ph}', (email,))
    return jsonify({'success': True})

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'ai': bool(ANTHROPIC_API_KEY)})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
