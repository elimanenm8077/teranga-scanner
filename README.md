# 🛡️ Teranga DEV — FiveM Security Scanner

**Web-based backdoor scanner for FiveM servers**
Developed by **Tarkalla** | Teranga RP — Pays de la Teranga

---

## 🔍 What it does

Scans FiveM server scripts for known backdoors and malicious code.
Upload your files directly from the browser — no installation required.

## 🎯 Supported formats

| Input | Description |
|---|---|
| `.lua` `.js` `.cfg` `.html` `.ttf` `.json` | Individual script files |
| 📁 Folder | Entire resource folder |
| `.zip` | Compressed archive |
| `.rar` | RAR archive |

## 🔴 Detected threats (30+ signatures)

- **Cipher Panel** / CipherCorp / NoctuaPanel
- **Blum Panel** (XOR bit.bxor technique)
- **GFX Panel** (gfxpanel.org) — confirmed on Teranga RP
- **KVAC Panel** (kvac.cz)
- **FiveMStatusCheck** (hlcm5alcv13)
- **Fake Font Payload** `.ttf` — confirmed on 17mov resources
- `string.char(108,111,97,100)` load() ASCII encoding
- `_G[string.char(...)]` load relay
- Auto-reinjection via SaveResourceFile
- ACE privilege escalation
- rcon/steam/mysql credential exfiltration
- NUI XSS / eval(atob()) / eval(d)
- Double base64 payloads
- Cipher hex byte tables
- And more...

## 🚀 Deploy on Railway

[![Deploy on Render](https://render.com/button.svg)](https://render.com/)

1. Fork this repo
2. Go to [railway.app](https://render.com/)
3. New Project → Deploy from GitHub
4. Select this repo → Deploy
5. Your scanner is live in 2 minutes ✅

## 🛠️ Run locally

```bash
pip install flask rarfile gunicorn
python app.py
```

Then open `http://localhost:5000`

## 📋 Risk levels

| Level | Score | Description |
|---|---|---|
| 🔴 CRITICAL | > 7 | Confirmed backdoor — delete immediately |
| 🟡 MEDIUM | 4–7 | Suspicious code — manual review required |
| 🟢 LOW | 1–3 | Minor alert — likely false positive |
| ✅ CLEAN | 0 | No threats detected |

## ⚠️ Disclaimer

This tool is based on research of real backdoors found on FiveM servers (2020–2026).
Always scan scripts before installing them on your server — even paid scripts can be infected if downloaded from unofficial sources.

---

**Teranga DEV © 2026 — Tarkalla | Teranga RP**
