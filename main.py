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
KONTOPLAN = []
FIRMA_SETTINGS = {}

PRIORITAET_MAP = {
    "normal": "Kein Eiltempo",
    "niedrig": "Kein Eiltempo",
    "low": "Kein Eiltempo",
    "mittel": "Diese Woche",
    "medium": "Diese Woche",
    "hoch": "Dringend",
    "high": "Dringend",
    "urgent": "Dringend",
    "kritisch": "Dringend",
    "dringend": "Dringend",
    "diese woche": "Diese Woche",
    "kein eiltempo": "Kein Eiltempo",
    "überfällig": "Überfällig",
}

def normalisiere_prioritaet(wert):
    if not wert:
        return "Kein Eiltempo"
    return PRIORITAET_MAP.get(wert.lower().strip(), "Kein Eiltempo")

def berechne_betraege(brutto, mwst_satz):
    """Berechnet Netto und Vorsteuer aus Bruttobetrag"""
    if not brutto or brutto == 0:
        return 0, 0, 0
    faktor = 1 + (mwst_satz / 100)
    netto = round(brutto / faktor, 2)
    vorsteuer = round(brutto - netto, 2)
    return netto, vorsteuer, brutto

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
    global KONTOPLAN, FIRMA_SETTINGS
    try:
        url = f"{BELEGFLUSS_URL}/api/public/agent/kontoplan"
        headers = {"x-agent-key": BELEGFLUSS_KEY}
        antwort = requests.get(url, headers=headers, timeout=30)
        if antwort.status_code in [200, 201]:
            daten = antwort.json()
            # Kontoplan und Firmaeinstellungen trennen
            if isinstance(daten, dict):
                KONTOPLAN = daten.get("konten", [])
                FIRMA_SETTINGS = daten.get("settings", {})
            else:
                KONTOPLAN = daten
                FIRMA_SETTINGS = {}
            log(f"✅ Kontoplan geladen: {len(KONTOPLAN)} Konten")
            log(f"✅ Vorsteuer aktiv: {FIRMA_SETTINGS.get('vorsteuer_aktiv', True)}")
            log(f"✅ Vorsteuer-Konto: {FIRMA_SETTINGS.get('konto_vorsteuer', '1170')}")
        else:
            log(f"⚠️ Kontoplan Fehler: {antwort.status_code}")
    except Exception as e:
        log(f"⚠️ Kontoplan Exception: {e}")

def kontoplan_als_text():
    if not KONTOPLAN:
        return "Kein Kontoplan verfügbar."
    aufwand = [k for k in KONTOPLAN if k.get("typ") == "Aufwand"]
    return "\n".join([f"{k.get('kontonummer')} = {k.get('kontobezeichnung')}" for k in aufwand])

def lade_pdf_hoch(pdf_daten, dateiname):
    try:
        url = f"{BELEGFLUSS_URL}/api/public/agent/upload"
        headers = {"x-agent-key": BELEGFLUSS_KEY}
        files = {"file": (dateiname, pdf_daten, "application/pdf")}
        antwort = requests.post(url, headers=headers, files=files, timeout=60)
        if antwort.status_code in [200, 201]:
            log("✅ PDF hochgeladen")
            return antwort.json().get("url", "")
        else:
            log(f"❌ Upload Fehler: {antwort.status_code}")
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
    vorsteuer_aktiv = FIRMA_SETTINGS.get("vorsteuer_aktiv", True)
    konto_vorsteuer = FIRMA_SETTINGS.get("konto_vorsteuer", "1170")

    prompt = f"""Du bist ein erfahrener Schweizer Treuhänder mit 20 Jahren KMU-Erfahrung.

AUFGABE: Analysiere dieses Dokument für die Buchhaltung.

MWST-EINSTELLUNG dieser Firma:
- Vorsteuerabzug: {"JA - Dreiecksbuchung anwenden" if vorsteuer_aktiv else "NEIN - Saldosteuersatz"}
- Vorsteuer-Konto: {konto_vorsteuer}

AUFWANDKONTEN:
{kontoplan_text}

KONTIERUNGSREGELN:
- Werbung, Plakate, APG, Marketing → 6600
- Reinigung, Reinigungsservice → 6040
- Telefon, Internet, Swisscom, Salt → 6510
- Miete, Raumkosten → 6000
- Versicherungen → 6300
- Strom, Gas, Wasser → 6400
- Fahrzeuge, Treibstoff → 6200
- Fahrzeugleasing → 6260
- Büromaterial → 6500
- IT, Software → 6570
- Beratung, Treuhand, Anwalt → 6530
- Löhne → 5000
- AHV, Sozialversicherungen → 5700
- Material, Waren → 4000
- Fremdarbeiten → 4060
- Bankspesen → 6940
- Maschinenunterhalt → 6100
- Kreditoren (Haben) IMMER → 2000

BUCHUNGSLOGIK:
{"Dreiecksbuchung: Aufwandkonto (Netto) SOLL + Vorsteuer " + konto_vorsteuer + " SOLL + Kreditoren 2000 (Brutto) HABEN" if vorsteuer_aktiv else "Zweizeilenbuchung: Aufwandkonto (Brutto inkl. MWST) SOLL + Kreditoren 2000 HABEN"}

DOKUMENT:
Absender: {absender}
Betreff: {betreff}
Inhalt: {pdf_text[:3000]}

Antworte NUR mit validem JSON:
{{
  "typ": "Rechnung",
  "absender_name": "exakter Firmenname",
  "betrag_brutto": 0.00,
  "mwst_satz": 8.1,
  "prioritaet": "Kein Eiltempo",
  "frist": "YYYY-MM-DD oder null",
  "konto_aufwand": "KONTONUMMER",
  "konto_kredit": "2000",
  "zusammenfassung": "1 Satz auf Deutsch"
}}"""

    try:
        antwort = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        text = antwort.content[0].text.strip()
        if "```" in text:
            parts = text.split("```")
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())
        result["prioritaet"] = normalisiere_prioritaet(result.get("prioritaet", ""))

        # Beträge berechnen
        brutto = result.get("betrag_brutto", 0)
        mwst_satz = result.get("mwst_satz", 8.1)
        netto, vorsteuer, brutto = berechne_betraege(brutto, mwst_satz)
        result["betrag_netto"] = netto
        result["betrag_brutto"] = brutto
        result["mwst_betrag"] = vorsteuer
        result["konto_vorsteuer"] = konto_vorsteuer if vorsteuer_aktiv else None
        result["vorsteuer_aktiv"] = vorsteuer_aktiv

        log(f"✅ Konto {result.get('konto_aufwand')} | Netto: {netto} | Vorsteuer: {vorsteuer} | Brutto: {brutto}")
        return result
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
            "betrag": analyse.get("betrag_netto", 0),
            "betrag_netto": analyse.get("betrag_netto", 0),
            "betrag_brutto": analyse.get("betrag_brutto", 0),
            "mwst_satz": analyse.get("mwst_satz", 8.1),
            "mwst_betrag": analyse.get("mwst_betrag", 0),
            "frist": analyse.get("frist"),
            "konto_aufwand": analyse.get("konto_aufwand"),
            "konto_kredit": analyse.get("konto_kredit", "2000"),
            "konto_vorsteuer": analyse.get("konto_vorsteuer"),
            "agent_zusammenfassung": analyse.get("zusammenfassung", ""),
            "agent_verarbeitet": True,
            "original_dateiname": dateiname,
            "datum": mail_datum,
            "pdf_url": pdf_url
        }
        antwort = requests.post(url, headers=headers, json=daten, timeout=30)
        log(f"📡 Status: {antwort.status_code}")
        if antwort.status_code in [200, 201]:
            log("✅ Gespeichert!")
            return antwort.json()
        else:
            log(f"❌ Fehler: {antwort.status_code} - {antwort.text[:200]}")
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
        log("📤 Lade PDF hoch...")
        pdf_url = lade_pdf_hoch(pdf["daten"], pdf["dateiname"])
        pdf_text = lese_pdf_text(pdf["daten"])
        if not pdf_text:
            log("⚠️ PDF nicht lesbar")
            continue
        log("🤖 Analysiere mit Claude + Kontoplan...")
        analyse = analysiere_mit_claude(pdf_text, absender, betreff)
        if not analyse:
            log("❌ Analyse fehlgeschlagen")
            continue
        ergebnis = speichere_in_belegfluss(analyse, pdf["dateiname"], mail_daten["datum"], pdf_url)
        if ergebnis:
            logge_aktion("DOKUMENT_VERARBEITET", f"{pdf['dateiname']} | {analyse.get('typ')} | Netto: {analyse.get('betrag_netto')} CHF | Konto {analyse.get('konto_aufwand')}", ergebnis.get("id"))
            log("🎉 Erfolgreich gespeichert!")

def haupt_schleife():
    log("🚀 Belegfluss Agent gestartet")
    log(f"📬 Überwache: {IMAP_EMAIL}")
    log(f"🔄 Intervall: {CHECK_INTERVAL}s")
    log(f"🌐 URL: {BELEGFLUSS_URL}")
    log(f"🔑 Key: {'OK' if BELEGFLUSS_KEY else 'FEHLT!'}")
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
