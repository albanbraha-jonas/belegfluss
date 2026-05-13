import imaplib
import email
import os
import time
import requests
import anthropic
import PyPDF2
import io
import json
from datetime import datetime

IMAP_SERVER = os.environ.get("IMAP_SERVER", "imap.hostinger.com")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
IMAP_EMAIL = os.environ.get("IMAP_EMAIL")
IMAP_PASSWORD = os.environ.get("IMAP_PASSWORD")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
BELEGFLUSS_URL = "https://clear-board-hub.lovable.app"
BELEGFLUSS_KEY = os.environ.get("BELEGFLUSS_KEY", "")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "60"))

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Kontoplan wird einmalig beim Start geladen
KONTOPLAN = []

def log(message):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}")

def verbinde_imap():
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        mail.login(IMAP_EMAIL, IMAP_PASSWORD)
        log("✅ IMAP Verbindung erfolgreich")
        return mail
    except Exception as e:
        log(f"❌ IMAP Fehler: {e}")
        return None

def hole_ungelesene_mails(mail):
    mail.select("INBOX")
    _, nachrichten = mail.search(None, "UNSEEN")
    return nachrichten[0].split()

def lade_kontoplan():
    """Lädt den Kontoplan einmalig beim Start"""
    global KONTOPLAN
    try:
        url = f"{BELEGFLUSS_URL}/api/public/agent/kontoplan"
        headers = {"x-agent-key": BELEGFLUSS_KEY}
        antwort = requests.get(url, headers=headers, timeout=30)
        if antwort.status_code in [200, 201]:
            KONTOPLAN = antwort.json()
            log(f"✅ Kontoplan geladen: {len(KONTOPLAN)} Konten")
        else:
            log(f"⚠️ Kontoplan nicht geladen: {antwort.status_code}")
    except Exception as e:
        log(f"⚠️ Kontoplan Fehler: {e}")

def kontoplan_als_text():
    """Formatiert den Kontoplan als Text für Claude"""
    if not KONTOPLAN:
        return "Kein Kontoplan verfügbar. Verwende Standard KMU-Konten."
    zeilen = []
    for konto in KONTOPLAN:
        zeilen.append(f"{konto.get('kontonummer', '')} - {konto.get('kontobezeichnung', '')} ({konto.get('typ', '')})")
    return "\n".join(zeilen)

def lade_pdf_hoch(pdf_daten, dateiname):
    """Lädt PDF in Supabase Storage hoch und gibt URL zurück"""
    try:
        url = f"{BELEGFLUSS_URL}/api/public/agent/upload"
        headers = {"x-agent-key": BELEGFLUSS_KEY}
        files = {"file": (dateiname, pdf_daten, "application/pdf")}
        antwort = requests.post(url, headers=headers, files=files, timeout=60)
        log(f"📤 Upload Status: {antwort.status_code}")
        if antwort.status_code in [200, 201]:
            daten = antwort.json()
            pdf_url = daten.get("url", "")
            log(f"✅ PDF hochgeladen: {pdf_url[:50]}...")
            return pdf_url
        else:
            log(f"❌ Upload Fehler: {antwort.text[:100]}")
            return None
    except Exception as e:
        log(f"❌ Upload Exception: {e}")
        return None

def extrahiere_pdf(mail, mail_id):
    _, daten = mail.fetch(mail_id, "(RFC822)")
    nachricht = email.message_from_bytes(daten[0][1])
    absender = nachricht.get("From", "Unbekannt")
    betreff = nachricht.get("Subject", "Kein Betreff")
    datum = nachricht.get("Date", "")
    pdfs = []
    for teil in nachricht.walk():
        if teil.get_content_type() == "application/pdf":
            pdf_daten = teil.get_payload(decode=True)
            dateiname = teil.get_filename() or "dokument.pdf"
            pdfs.append({"daten": pdf_daten, "dateiname": dateiname})
    return {"absender": absender, "betreff": betreff, "datum": datum, "pdfs": pdfs}

def lese_pdf_text(pdf_daten):
    try:
        pdf_datei = io.BytesIO(pdf_daten)
        pdf_leser = PyPDF2.PdfReader(pdf_datei)
        text = ""
        for seite in pdf_leser.pages:
            text += seite.extract_text() or ""
        return text
    except Exception as e:
        log(f"❌ PDF Lesefehler: {e}")
        return ""

def analysiere_mit_claude(pdf_text, absender, betreff):
    kontoplan_text = kontoplan_als_text()

    prompt = f"""Du bist ein erfahrener Schweizer Buchhalter und Treuhänder mit 20 Jahren Erfahrung.
Analysiere dieses Dokument und kontiere es korrekt gemäss dem Kontoplan der Firma.

KONTOPLAN DER FIRMA:
{kontoplan_text}

DOKUMENT:
Absender: {absender}
Betreff: {betreff}
Inhalt:
{pdf_text[:3000]}

AUFGABE:
Analysiere das Dokument und wähle die korrekten Kontonummern aus dem obigen Kontoplan.
Denke wie ein erfahrener Schweizer Buchhalter:
- Telefonrechnung → Telekommunikationsaufwand
- Miete → Mietaufwand
- Büromaterial → Büroaufwand
- Versicherung → Versicherungsaufwand
- Lohnabrechnung → Personalaufwand
- Kreditoren immer auf Konto 2000 (Kreditoren)

Antworte NUR mit diesem JSON (keine weiteren Texte, keine Erklärungen):
{{
  "typ": "Rechnung|Mahnung|Behoerde|MWST|AHV|Vertrag|Sonstiges",
  "absender_name": "Exakter Name des Absenders aus dem Dokument",
  "betrag": 0.00,
  "mwst_satz": 8.1,
  "mwst_betrag": 0.00,
  "frist": "YYYY-MM-DD oder null",
  "konto_aufwand": "Kontonummer aus dem Kontoplan",
  "konto_kredit": "2000",
  "prioritaet": "Dringend|Diese Woche|Kein Eiltempo",
  "zusammenfassung": "1-2 Sätze Zusammenfassung auf Deutsch"
}}"""

    try:
        antwort = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        text = antwort.content[0].text.strip()
        if "```" in text:
            parts = text.split("```")
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception as e:
        log(f"❌ Claude Fehler: {e}")
        return None

def speichere_in_belegfluss(analyse, dateiname, mail_datum, pdf_url=None):
    try:
        url = f"{BELEGFLUSS_URL}/api/public/agent/dokument"
        headers = {
            "x-agent-key": BELEGFLUSS_KEY,
            "Content-Type": "application/json"
        }
        daten = {
            "absender": analyse.get("absender_name", "Unbekannt"),
            "typ": analyse.get("typ", "Sonstiges"),
            "prioritaet": analyse.get("prioritaet", "Kein Eiltempo"),
            "betrag": analyse.get("betrag", 0),
            "mwst_satz": analyse.get("mwst_satz", 8.1),
            "mwst_betrag": analyse.get("mwst_betrag", 0),
            "frist": analyse.get("frist"),
            "konto_aufwand": analyse.get("konto_aufwand", "4000"),
            "konto_kredit": analyse.get("konto_kredit", "2000"),
            "agent_zusammenfassung": analyse.get("zusammenfassung", ""),
            "agent_verarbeitet": True,
            "original_dateiname": dateiname,
            "datum": mail_datum,
            "pdf_url": pdf_url
        }
        antwort = requests.post(url, headers=headers, json=daten, timeout=30)
        log(f"📡 Status: {antwort.status_code}")
        if antwort.status_code in [200, 201]:
            log(f"✅ Gespeichert: {dateiname}")
            return antwort.json()
        else:
            log(f"❌ Fehler: {antwort.status_code} - {antwort.text[:100]}")
            return None
    except Exception as e:
        log(f"❌ Speicherfehler: {e}")
        return None

def logge_aktion(aktion, details, dokument_id=None):
    try:
        url = f"{BELEGFLUSS_URL}/api/public/agent/log"
        headers = {"x-agent-key": BELEGFLUSS_KEY, "Content-Type": "application/json"}
        daten = {"aktion": aktion, "details": details, "dokument_id": dokument_id}
        requests.post(url, headers=headers, json=daten, timeout=10)
    except:
        pass

def verarbeite_mail(mail, mail_id):
    log(f"📧 Mail ID: {mail_id.decode()}")
    mail_daten = extrahiere_pdf(mail, mail_id)
    absender = mail_daten["absender"]
    betreff = mail_daten["betreff"]
    log(f"📨 Von: {absender} | Betreff: {betreff}")
    if not mail_daten["pdfs"]:
        log("⚠️ Kein PDF – überspringe")
        return
    for pdf in mail_daten["pdfs"]:
        log(f"📄 PDF: {pdf['dateiname']}")

        # 1. PDF hochladen
        log("📤 Lade PDF hoch...")
        pdf_url = lade_pdf_hoch(pdf["daten"], pdf["dateiname"])

        # 2. PDF Text lesen
        pdf_text = lese_pdf_text(pdf["daten"])
        if not pdf_text:
            log("⚠️ PDF nicht lesbar")
            continue

        # 3. Mit Claude analysieren (mit Kontoplan)
        log("🤖 Analysiere mit Claude + Kontoplan...")
        analyse = analysiere_mit_claude(pdf_text, absender, betreff)
        if not analyse:
            log("❌ Analyse fehlgeschlagen")
            continue

        log(f"✅ {analyse['typ']} | {analyse['betrag']} CHF | Konto {analyse['konto_aufwand']}/{analyse['konto_kredit']} | {analyse['prioritaet']}")

        # 4. In Belegfluss speichern
        ergebnis = speichere_in_belegfluss(analyse, pdf["dateiname"], mail_daten["datum"], pdf_url)
        if ergebnis:
            logge_aktion(
                "DOKUMENT_VERARBEITET",
                f"{pdf['dateiname']} | {analyse['typ']} | {analyse['betrag']} CHF | Konto {analyse['konto_aufwand']}",
                ergebnis.get("id")
            )
            log("🎉 Erfolgreich gespeichert!")

def haupt_schleife():
    log("🚀 Belegfluss Agent gestartet")
    log(f"📬 Überwache: {IMAP_EMAIL}")
    log(f"🔄 Intervall: {CHECK_INTERVAL}s")
    log(f"🌐 URL: {BELEGFLUSS_URL}")
    log(f"🔑 Key: {'OK' if BELEGFLUSS_KEY else 'FEHLT!'}")

    # Kontoplan einmalig laden
    lade_kontoplan()

    while True:
        try:
            mail = verbinde_imap()
            if mail:
                ungelesene = hole_ungelesene_mails(mail)
                if ungelesene:
                    log(f"📬 {len(ungelesene)} neue Mail(s)")
                    for mail_id in ungelesene:
                        verarbeite_mail(mail, mail_id)
                else:
                    log("📭 Keine neuen E-Mails")
                mail.logout()
        except Exception as e:
            log(f"❌ Fehler: {e}")
        log(f"⏳ Warte {CHECK_INTERVAL}s...")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    haupt_schleife()
