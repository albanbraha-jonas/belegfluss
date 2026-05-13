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
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.units import mm
from PyPDF2 import PdfReader, PdfWriter

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

def berechne_betraege(betrag_netto, mwst_satz):
    """Berechnet MWST und Brutto aus Nettobetrag"""
    if not betrag_netto or betrag_netto == 0:
        return 0, 0, 0
    mwst = round(betrag_netto * (mwst_satz / 100), 2)
    brutto = round(betrag_netto + mwst, 2)
    netto = round(betrag_netto, 2)
    return netto, mwst, brutto

def log(message):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}")

def erstelle_buchungsstempel(analyse):
    """Erstellt Buchungsstempel als PDF overlay"""
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=(210*mm, 297*mm))

    vorsteuer_aktiv = analyse.get("vorsteuer_aktiv", True)
    konto_aufwand = analyse.get("konto_aufwand", "")
    konto_vorsteuer = analyse.get("konto_vorsteuer", "1170")
    konto_kredit = analyse.get("konto_kredit", "2000")
    betrag_netto = analyse.get("betrag_netto", 0)
    mwst_betrag = analyse.get("mwst_betrag", 0)
    betrag_brutto = analyse.get("betrag_brutto", 0)
    mwst_satz = analyse.get("mwst_satz", 8.1)
    datum = datetime.now().strftime("%d.%m.%Y")

    # Stempel oben rechts
    x = 118*mm
    y = 238*mm
    breite = 88*mm
    hoehe = 58*mm if vorsteuer_aktiv else 48*mm

    # Weisser Hintergrund
    c.setFillColor(white)
    c.rect(x, y, breite, hoehe, fill=1, stroke=0)

    # Roter Rahmen
    c.setStrokeColor(HexColor("#CC0000"))
    c.setLineWidth(1.5)
    c.rect(x, y, breite, hoehe, fill=0, stroke=1)

    # Roter Header
    c.setFillColor(HexColor("#CC0000"))
    c.rect(x, y + hoehe - 9*mm, breite, 9*mm, fill=1, stroke=0)

    # Header Text
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(x + 3*mm, y + hoehe - 6*mm, "BUCHUNGSBELEG")
    c.drawRightString(x + breite - 3*mm, y + hoehe - 6*mm, datum)

    # Spaltenheader
    zeile_y = y + hoehe - 14*mm
    c.setFillColor(HexColor("#333333"))
    c.setFont("Helvetica-Bold", 6)
    c.drawString(x + 2*mm, zeile_y, "Typ")
    c.drawString(x + 12*mm, zeile_y, "Konto")
    c.drawString(x + 28*mm, zeile_y, "Bezeichnung")
    c.drawRightString(x + breite - 2*mm, zeile_y, "CHF")

    # Trennlinie
    zeile_y -= 2.5*mm
    c.setStrokeColor(HexColor("#CCCCCC"))
    c.setLineWidth(0.5)
    c.line(x + 1*mm, zeile_y, x + breite - 1*mm, zeile_y)
    zeile_y -= 5*mm

    c.setFont("Helvetica", 6.5)
    abstand = 6.5*mm

    # SOLL: Aufwandkonto (Netto)
    c.setFillColor(HexColor("#CC0000"))
    c.setFont("Helvetica-Bold", 6)
    c.drawString(x + 2*mm, zeile_y, "SOLL")
    c.setFillColor(black)
    c.setFont("Helvetica", 6.5)
    c.drawString(x + 12*mm, zeile_y, konto_aufwand)
    c.drawString(x + 28*mm, zeile_y, "Aufwand")
    c.drawRightString(x + breite - 2*mm, zeile_y, f"{betrag_netto:,.2f}")
    zeile_y -= abstand

    if vorsteuer_aktiv:
        # SOLL: Vorsteuer
        c.setFillColor(HexColor("#CC0000"))
        c.setFont("Helvetica-Bold", 6)
        c.drawString(x + 2*mm, zeile_y, "SOLL")
        c.setFillColor(black)
        c.setFont("Helvetica", 6.5)
        c.drawString(x + 12*mm, zeile_y, konto_vorsteuer)
        c.drawString(x + 28*mm, zeile_y, f"Vorsteuer {mwst_satz}%")
        c.drawRightString(x + breite - 2*mm, zeile_y, f"{mwst_betrag:,.2f}")
        zeile_y -= abstand

    # HABEN: Kreditoren (Brutto)
    c.setFillColor(HexColor("#006600"))
    c.setFont("Helvetica-Bold", 6)
    c.drawString(x + 2*mm, zeile_y, "HABEN")
    c.setFillColor(black)
    c.setFont("Helvetica", 6.5)
    c.drawString(x + 12*mm, zeile_y, konto_kredit)
    c.drawString(x + 28*mm, zeile_y, "Kreditoren")
    c.drawRightString(x + breite - 2*mm, zeile_y, f"{betrag_brutto:,.2f}")

    # Trennlinie vor Footer
    zeile_y -= 3*mm
    c.setStrokeColor(HexColor("#CC0000"))
    c.setLineWidth(0.5)
    c.line(x + 1*mm, zeile_y, x + breite - 1*mm, zeile_y)
    zeile_y -= 4*mm

    # Footer
    c.setFont("Helvetica-Bold", 5.5)
    c.setFillColor(HexColor("#666666"))
    c.drawString(x + 2*mm, zeile_y, f"Kontiert: {datum}  |  KI-Agent Belegfluss")

    c.save()
    packet.seek(0)
    return packet

def stempel_auf_pdf(pdf_daten, analyse):
    """Fügt Buchungsstempel auf erste Seite des PDFs ein"""
    try:
        stempel_pdf = erstelle_buchungsstempel(analyse)
        stempel_reader = PdfReader(stempel_pdf)
        stempel_seite = stempel_reader.pages[0]

        original_reader = PdfReader(io.BytesIO(pdf_daten))
        writer = PdfWriter()

        for i, seite in enumerate(original_reader.pages):
            if i == 0:
                seite.merge_page(stempel_seite)
            writer.add_page(seite)

        output = io.BytesIO()
        writer.write(output)
        output.seek(0)
        log("✅ Buchungsstempel gesetzt")
        return output.read()
    except Exception as e:
        log(f"❌ Stempel Fehler: {e} – verwende Original")
        return pdf_daten

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
            if isinstance(daten, dict):
                KONTOPLAN = daten.get("konten", [])
                FIRMA_SETTINGS = daten.get("settings", {})
            else:
                KONTOPLAN = daten
                FIRMA_SETTINGS = {}
            log(f"✅ Kontoplan geladen: {len(KONTOPLAN)} Konten")
            log(f"✅ Vorsteuer aktiv: {FIRMA_SETTINGS.get('vorsteuer_aktiv', True)}")
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

AUFGABE: Analysiere dieses Dokument und extrahiere die Buchhaltungsdaten.

WICHTIG BETRÄGE:
- Gib immer den NETTO-Betrag zurück (ohne MWST)
- Beispiel: Total CHF 4500.00 + MWST 8.1% CHF 364.50 = Brutto CHF 4864.50 → betrag_netto = 4500.00
- NIEMALS den Bruttobetrag als Netto angeben!

MWST-EINSTELLUNG dieser Firma:
- Vorsteuerabzug: {"JA" if vorsteuer_aktiv else "NEIN"}

AUFWANDKONTEN (wähle das passendste):
{kontoplan_text}

KONTIERUNGSREGELN:
- Werbung, APG, Marketing, Inserate → 6600
- Reinigung, Reinigungsservice → 6040
- Telefon, Internet, Swisscom, Salt, Sunrise → 6510
- Miete, Raumkosten → 6000
- Versicherungen → 6300
- Strom, Gas, Wasser → 6400
- Fahrzeuge, Treibstoff → 6200
- Büromaterial, Drucksachen → 6500
- IT, Software, EDV → 6570
- Beratung, Treuhand, Buchhaltung, Anwalt → 6530
- Löhne → 5000
- AHV, Sozialversicherungen → 5700
- Material, Waren → 4000
- Fremdarbeiten → 4060
- Bankspesen → 6940
- Kreditoren IMMER → 2000

DOKUMENT:
Absender: {absender}
Betreff: {betreff}
Inhalt:
{pdf_text[:3000]}

Antworte NUR mit validem JSON, kein Markdown:
{{
  "typ": "Rechnung",
  "absender_name": "exakter Firmenname aus Dokument",
  "betrag_netto": 0.00,
  "mwst_satz": 8.1,
  "frist": "YYYY-MM-DD oder null",
  "konto_aufwand": "KONTONUMMER",
  "konto_kredit": "2000",
  "prioritaet": "Kein Eiltempo",
  "zusammenfassung": "1 Satz auf Deutsch was diese Rechnung ist"
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

        # Priorität normalisieren
        result["prioritaet"] = normalisiere_prioritaet(result.get("prioritaet", ""))

        # Beträge berechnen: Netto → MWST → Brutto
        betrag_netto = result.get("betrag_netto", 0)
        mwst_satz = result.get("mwst_satz", 8.1)
        netto, mwst_betrag, brutto = berechne_betraege(betrag_netto, mwst_satz)

        result["betrag_netto"] = netto
        result["mwst_betrag"] = mwst_betrag
        result["betrag_brutto"] = brutto
        result["konto_vorsteuer"] = konto_vorsteuer if vorsteuer_aktiv else None
        result["vorsteuer_aktiv"] = vorsteuer_aktiv

        log(f"✅ Konto {result.get('konto_aufwand')} | Netto: {netto} + MWST: {mwst_betrag} = Brutto: {brutto}")
        return result
    except Exception as e:
        log(f"❌ Claude Fehler: {e}")
        return None

def speichere_in_belegfluss(analyse, dateiname, mail_datum, pdf_url=None):
    try:
        url = f"{BELEGFLUSS_URL}/api/public/agent/dokument"
        headers = {"x-agent-key": BELEGFLUSS_KEY, "Content-Type": "application/json"}
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

        # 1. PDF Text lesen
        pdf_text = lese_pdf_text(pdf["daten"])
        if not pdf_text:
            log("⚠️ PDF nicht lesbar")
            continue

        # 2. Mit Claude analysieren
        log("🤖 Analysiere mit Claude + Kontoplan...")
        analyse = analysiere_mit_claude(pdf_text, absender, betreff)
        if not analyse:
            log("❌ Analyse fehlgeschlagen")
            continue

        log(f"📊 Buchung: {analyse.get('konto_aufwand')} CHF {analyse.get('betrag_netto')} + "
            f"1170 CHF {analyse.get('mwst_betrag')} / 2000 CHF {analyse.get('betrag_brutto')}")

        # 3. Buchungsstempel auf PDF setzen
        log("🖊️ Setze Buchungsstempel...")
        pdf_mit_stempel = stempel_auf_pdf(pdf["daten"], analyse)

        # 4. Gestempeltes PDF hochladen
        log("📤 Lade gestempeltes PDF hoch...")
        dateiname_gestempelt = f"kontiert_{pdf['dateiname']}"
        pdf_url = lade_pdf_hoch(pdf_mit_stempel, dateiname_gestempelt)

        # 5. In Belegfluss speichern
        ergebnis = speichere_in_belegfluss(analyse, pdf["dateiname"], mail_daten["datum"], pdf_url)
        if ergebnis:
            logge_aktion(
                "DOKUMENT_VERARBEITET",
                f"{pdf['dateiname']} | Netto: {analyse.get('betrag_netto')} | MWST: {analyse.get('mwst_betrag')} | Brutto: {analyse.get('betrag_brutto')} | Konto {analyse.get('konto_aufwand')}",
                ergebnis.get("id")
            )
            log("🎉 Erfolgreich gespeichert mit Buchungsstempel!")

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
