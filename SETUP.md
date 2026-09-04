# Schritt für Schritt: Community-Banking einrichten

Arbeite die Schritte **in dieser Reihenfolge** ab. Nicht mit n8n parallel
importieren, solange der erste saubere Pull in Odoo nicht steht — sonst
Duplikate.

Menüpfade gelten für **Odoo 19 Community** nach Installation der OCA-Module
(App heißt weiter *Fakturierung / Invoicing*, nicht Enterprise-Buchhaltung).

---

## Schritt 0 — GoCardless ist bei dir ein Clearing-Konto

Du ziehst Lastschriften ein. Das ist **GoCardless Payments**, nicht
Open Banking.

- Journal `GC EUR` auf einem abstimmbaren Clearing-Konto.
- Jeder Einzug erscheint dort (auch wenn er noch submitted ist, Betrag 0).
- Schlägt der Einzug fehl, bleibt dieselbe Zeile und der Status/Betrag
  wird nachgezogen (failed → 0).
- Die Auszahlung an die Hausbank ist eine Minus-Zeile plus Gebühren.
  Clearing = 0. Die Hausbank bucht den Eingang über ihren eigenen Feed
  und ihr stimmt Payout gegen Bankeingang über Geldtransit ab.

Nicht das OCA-Modul *GoCardless Bank Account Data* installieren.

---

## Schritt 1 — Zugangsdaten bereitlegen

Nichts in Git oder in n8n-Notes speichern. Nur ins Odoo-Provider-Formular.

1. **PayPal**
   - [developer.paypal.com](https://developer.paypal.com) → Apps & Credentials → **Live**
   - App anlegen, **Client ID** und **Secret** kopieren
   - Scope: Transaction Search / Reporting (Business-Konto)
2. **ZEN.COM**
   - Bei ZEN einen **Transfers-API-Key** beantragen (nicht den Terminal-Key
     aus my.zen.com Payments)
   - Wallet-IBAN und Währung notieren
   - Optional: Account-UUID, falls du mehrere Wallets hast
3. **GoCardless Payments**
   - Developers → Access Token (Live)
   - Webhook-Endpoint anlegen: `https://DEINE-ODOO/gocardless/payments/webhook`
   - Endpoint-Secret notieren
   - Events: payments (alle), payouts (paid, bounced), refunds
4. **Jeeves**
   - Eine aktuelle Activity-CSV exportieren (*Activity and Exports*)
   - Spalten prüfen: Transaction ID, Posted Date, Merchant, Amount, Status
5. **Bank** (falls kein BAD)
   - Einen CAMT.053 oder CSV-Auszug der Hausbank bereitlegen

---

## Schritt 2 — Module auf den Server legen

Auf dem Odoo-Host, neben deiner Odoo-Installation (Pfade anpassen):

```bash
mkdir -p /opt/odoo/extra
cd /opt/odoo/extra

git clone --branch 19.0 --depth 1 https://github.com/OCA/account-reconcile
git clone --branch 19.0 --depth 1 https://github.com/OCA/bank-statement-import
git clone --branch cursor/community-banking-stack-f606 https://github.com/xxxchris0815/odoo_banking
```

In der Odoo-Konfiguration (`odoo.conf` oder Docker `ADDONS_PATH`):

```
addons_path = /opt/odoo/odoo/addons,/opt/odoo/extra/account-reconcile,/opt/odoo/extra/bank-statement-import,/opt/odoo/extra/odoo_banking/addons
```

Odoo neu starten, danach Apps → *Apps aktualisieren*.

Wenn du **kein** GoCardless-BAD nutzt, ist das in Ordnung: das Meta-Modul
hängt nicht daran.

---

## Schritt 3 — Module in Odoo installieren

1. Apps → Filter *Apps* entfernen (auch *Technisch* anzeigen).
2. Installieren:
   - `account_reconcile_oca`
   - `account_statement_import_online_paypal`
   - `account_statement_import_online_zen`
   - `account_statement_import_online_gocardless_payments`
   - `account_statement_import_jeeves`
   - `account_statement_import_sheet_file`
   - optional `account_statement_import_online_gocardless`
   - optional `account_statement_import_camt`
   - zum Schluss `banking_community` (zieht die Pflicht-Abhängigkeiten)
3. Einstellungen → Benutzer → dein Benutzer:
   - Haken **Vollständige Buchhaltungsfunktionen anzeigen**
   - neu einloggen

Ohne diese Gruppe siehst du weder Journals im vollen Umfang noch die
Online-Provider.

---

## Schritt 4 — Kontenplan vorbereiten

Einmalig, passend zu SKR03/SKR04 (Nummern sind Beispiele):

| Zweck | SKR03 (Beispiel) | SKR04 (Beispiel) | Kontotyp |
| --- | --- | --- | --- |
| Geldtransit / interne Transfers | 1360 | 1460 | Umlaufvermögen, abstimmbar |
| GoCardless Clearing | 1362 | 1462 | Umlaufvermögen, abstimmbar |
| PayPal EUR | 1210 | 1810 | Bank / flüssige Mittel |
| ZEN EUR | 1215 | 1815 | Bank / flüssige Mittel |
| Hausbank | 1200 | 1800 | Bank |
| Jeeves Credit | 1665 | 3610 | Verbindlichkeit |
| Jeeves Cash (Prepaid) | 1218 | 1818 | Bank / flüssige Mittel |
| PayPal-Gebühren | 4970 | 6855 | Aufwand |
| ZEN-Gebühren / FX | 4972 | 6856 | Aufwand |

Wichtig:

- Geldtransit **abstimmbar** (`reconcile = True`).
- Jeeves Credit ist **kein** Bankguthaben.
- Pro Währung ein eigenes Geldkonto und Journal. Nie EUR und USD im
  selben Journal mischen.

---

## Schritt 5 — Bankkonten und Journals anlegen

Für **jedes** Konto:

1. Fakturierung → Konfiguration → Banken → Bankkonto hinzufügen.
2. Name, IBAN (bei PayPal z. B. die Merchant-E-Mail oder Merchant-ID),
   Währung.
3. Odoo legt ein Journal an. Journal öffnen und prüfen:
   - Typ: Bank
   - Währung fest eingestellt
   - Standard-Buchungskonto = Konto aus Schritt 4
   - Zwischenkonto / Outstanding: dasselbe Geldkonto (Community-Default)

Vorschlag für die Codes (kurz, eindeutig):

| Journal | Code | Konto |
| --- | --- | --- |
| Hausbank EUR | BANK | 1200 / 1800 |
| GoCardless Clearing | GC | 1362 / 1462 |
| PayPal EUR | PPAL | 1210 / 1810 |
| ZEN EUR | ZEN | 1215 / 1815 |
| Jeeves Credit EUR | JCRD | 1665 / 3610 |
| Jeeves Cash EUR | JCSH | 1218 / 1818 |

Zweite Währung = zweites Journal (`PPUSD`, `ZENUSD`, …).

---

## Schritt 6 — Provider am Journal koppeln

### 6a PayPal

1. Journal *PayPal EUR* bearbeiten.
2. Bank Feeds = **Online (OCA)**.
3. Provider **PayPal.com**, speichern.
4. Den erzeugten Provider öffnen:
   - API Base leer lassen (Live)
   - Username / Client ID + Password / Secret eintragen
   - Intervall z. B. 1 Stunde
   - Statement-Modus: täglich oder monatlich
5. Speichern.

### 6b ZEN.COM

1. Journal *ZEN EUR*, IBAN muss zur Wallet passen.
2. Bank Feeds = **Online (OCA)** → Provider **ZEN.COM**.
3. Provider öffnen:
   - Password = Transfers-API-Key
   - Username = Account-UUID (leer lassen, wenn die IBAN eindeutig ist)
   - API Base leer = Produktion
4. Speichern.

### 6c GoCardless Payments (Einzüge)

1. Journal *GoCardless Clearing*, Konto = Clearing aus Schritt 4
   (abstimmbar, nicht die Hausbank).
2. Bank Feeds = **Online (OCA)** → Provider **GoCardless Payments**.
3. Provider öffnen:
   - Password = Access Token
   - Passphrase = Webhook-Secret
   - API Base leer = Live, sonst `https://api-sandbox.gocardless.com`
   - Intervall 1 Stunde (Fallback, falls ein Webhook ausfällt)
4. In GoCardless den Webhook auf
   `https://DEINE-ODOO/gocardless/payments/webhook` zeigen.
5. Test: einen Einzug im Sandbox anlegen — Zeile mit `[submitted]` und
   Betrag 0. Nach Confirm: dieselbe Zeile, Betrag +. Nach Fail:
   dieselbe Zeile, `[failed]`, Betrag 0.

Payouts nicht zusätzlich per n8n auf dieses Journal schreiben. Die
Hausbank bekommt den Eingang aus ihrem eigenen Auszug.

### 6d Jeeves

Kein Online-Provider. Journal bleibt auf Datei-Import / undefiniert.
Import kommt in Schritt 9.

---

## Schritt 7 — Ersten historischen Pull machen (nur ein Journal)

Zuerst **ein** Journal, nicht alle gleichzeitig.

1. Fakturierung → Konfiguration → Online Bank Statement Providers.
2. z. B. PayPal öffnen → **Pull Online Bank Statement**.
3. Zeitraum: letzte 7 Tage (klein anfangen).
4. Pull.

Erfolg:

- Unter dem Journal erscheinen Kontoauszugszeilen.
- Beträge haben das richtige Vorzeichen (Eingang +, Ausgang −).
- Dieselbe Transaktion ein zweites Mal pullen erzeugt **keine** zweite Zeile.

Fehler:

- Provider-Chatter und Server-Log lesen.
- PayPal: Live-Credentials, nicht Sandbox.
- ZEN: Transfers-Key, nicht Terminal-Key; nur SETTLED kommt an.
- GoCardless Payments: Access Token, Webhook, Clearing-Journal — nicht BAD.

Wenn die 7 Tage sauber sind: denselben Provider für 90 Tage pullen.
Danach den Cron lassen (`Pull Online Bank Statements`, stündlich).

Erst danach das nächste Journal (ZEN, dann Bank).

---

## Schritt 8 — Hausbank ohne GoCardless

1. Journal *Hausbank* → Auszug importieren (Dashboard-Kachel).
2. CAMT.053: Modul `account_statement_import_camt` muss installiert sein.
3. Beliebige CSV: `account_statement_import_sheet_file` → zuerst unter
   Konfiguration → Statement Sheet Mappings ein Mapping anlegen
   (Datum, Betrag, Verwendungszweck, eindeutige ID-Spalte).
4. Testdatei mit 5 Zeilen, dann den Rest.

---

## Schritt 9 — Jeeves-CSV importieren

1. In Jeeves: Activity and Exports → Konto + Zeitraum → CSV.
2. Pending-Zeilen dürfen in der Datei bleiben, der Parser wirft sie weg.
3. In Odoo: Journal *Jeeves Credit* bzw. *Jeeves Cash* → Auszug importieren.
4. Dieselbe Datei nicht in ein anderes Journal laden.
5. Dieselbe Datei ein zweites Mal importieren: keine neuen Zeilen
   (gleiche Transaction ID).

Wenn Odoo die Datei nicht als Jeeves erkennt (ungewöhnliche Header):

- Spalten umbenennen nach `Transaction ID`, `Posted Date`, `Merchant`,
  `Amount`, `Currency`, `Status`, oder
- über Statement Sheet Mapping manuell mappen.

---

## Schritt 10 — Abstimmungmodelle

Fakturierung → Konfiguration → Abstimmungmodelle (Reconcile Models).

Mindestens diese drei:

### PayPal-Gebühr

- Label enthält `fee` / `Gebühr` / `paypal`
- Gegenkonto: PayPal-Gebührenaufwand
- Partner leer lassen

### Geldtransit (interne Transfers)

- Betrag gleich, Gegenjournal das Zielkonto, Toleranz ±1 Tag
- Gegenkonto: Geldtransit aus Schritt 4
- Für PayPal→Bank, ZEN→Bank, Jeeves-Settlement, **GoCardless-Payout→Bank**
  je eine Regel oder eine gemeinsame mit Betragsmatch

GoCardless-Payout: Zeile `[payout paid] POxxx` (−Netto) gegen den
Bankeingang (+Netto). Gebührenzeile gegen den Gebührenaufwand.

### Jeeves-Kartenumsatz (optional)

- Merchant-Name → Partner oder Aufwandskonto
- erst pflegen, wenn 20–30 Zeilen manuell abgestimmt sind und Muster klar sind

Dann Dashboard → Journal → **Abstimmen**. Erst die internen Transfers,
dann Gebühren, dann den Rest gegen Rechnungen.

---

## Schritt 10b — Rechnungen über das GoCardless-Clearing bezahlen

Ja. Das Clearing-Konto **ist** der Zahlungseingang auf der Rechnung.
Die Hausbank kommt erst später (Payout) und darf die Rechnung nicht
noch einmal bezahlen.

Zwei Wege, nimm **einen** pro Einzug, nicht beide:

### Weg A — Auszug gegen Rechnung (empfohlen)

1. Rechnung bleibt offen, bis GoCardless `confirmed` ist.
2. Im Journal `GC` steht die Zeile `[confirmed] …` mit +Betrag.
3. Dashboard → GC → **Abstimmen** → Zeile der offenen Rechnung zuordnen.
4. Rechnung = bezahlt, Clearing = belastet. Ein Buchungssatz.

Schlägt der Einzug vorher fehl, ist die Zeile 0 — die Rechnung bleibt
offen. Nichts zurückdrehen.

### Weg B — „Zahlung registrieren“ auf der Rechnung

1. Rechnung → Zahlung registrieren → Journal **GC**.
2. Am Journal GC: Liquiditätskonto **und** Ausstehende Eingänge = dasselbe
   Clearing-Konto. Sonst hängt die Zahlung auf einem dritten Konto.
3. Buchung: Soll Clearing, Haben Forderung.
4. Danach die GC-Auszugszeile (`confirmed`) gegen **diese Zahlung**
   abstimmen, nicht nochmal gegen die Rechnung.

Nur registrieren, wenn der Einzug wirklich `confirmed` ist. Bei
`submitted` noch nicht — sonst musst du die Zahlung stornieren, sobald
GoCardless `failed` setzt.

### Was du nicht tun darfst

- Rechnung über GC bezahlen **und** dieselbe Rechnung über die Hausbank
  nochmal bezahlen (Payout ist nur Geldtransit).
- Zahlung schon bei Mandat/submitted registrieren und den Fail ignorieren.
- n8n zusätzlich eine Zahlung auf die Rechnung schreiben.

Payout: nur GC −Netto gegen Bank +Netto. Die Rechnung ist da schon zu.

---

## Schritt 11 — Parallelbetrieb zur Cloud / zu n8n

Solange die Community-Instanz noch nicht die führende Buchhaltung ist:

1. n8n-Workflows auf **Pause**, nicht löschen.
2. In Community nur die Journals aus Schritt 5 füttern — nicht dieselben
   Cloud-Journals per XML-RPC weiter beschreiben.
3. Zwei volle Cron-Läufe (z. B. über Nacht) ohne neue Duplikate und ohne
   Provider-Fehler im Chatter.
4. Stichprobe: je 5 PayPal-, ZEN-, Bank- und Jeeves-Zeilen gegen das
   Originalportal prüfen.
5. Erst dann n8n-Buchungsnodes deaktivieren. Übrig bleiben darf:
   - Jeeves-CSV aus Mail/S3 nach Odoo legen
   - Mail, wenn ein Provider im Chatter einen Fehler postet

---

## Schritt 12 — Abnahme-Checkliste

Haken setzen, bevor du n8n endgültig abschaltest:

- [ ] Benutzer hat volle Buchhaltungsfunktionen
- [ ] Ein Journal pro Konto **und** Währung
- [ ] Jeeves Credit hängt am Verbindlichkeitskonto
- [ ] Geldtransit-Konto ist abstimmbar
- [ ] PayPal: 7-Tage-Pull ok, zweiter Pull ohne Duplikate
- [ ] ZEN: nur SETTLED, IBAN/UUID stimmt, Vorzeichen richtig
- [ ] GoCardless: Einzug sichtbar, Fail ändert dieselbe Zeile auf 0,
      Payout + Gebühr setzen Clearing auf 0
- [ ] Rechnung nur über GC-Clearing bezahlt, Payout nicht nochmal gegen
      dieselbe Rechnung
- [ ] Bank: CAMT/CSV oder anderer Bankfeed, nicht GoCardless BAD
- [ ] Jeeves: eine CSV, Pending weg, Re-Import ohne Duplikate
- [ ] PayPal-Auszahlung erscheint auf PayPal **und** Bank und geht über Geldtransit
- [ ] Abstimmungmodell Gebühren und Transfers greifen
- [ ] n8n schreibt keine Journalzeilen mehr

---

## Wenn etwas schiefgeht

| Symptom | Typische Ursache |
| --- | --- |
| Kein Menü „Online Bank Statement Providers“ | Gruppe volle Buchhaltung fehlt, oder `account_statement_import_online` nicht installiert |
| Provider-Feld am Journal fehlt | Journal-Typ ist nicht Bank |
| PayPal pull leer | Sandbox-Key, oder Zeitraum älter als 3 Jahre |
| ZEN 403 | Terminal-Key statt Transfers-Key |
| GoCardless-Einzug fehlt | Access Token Live/Sandbox verdreht, oder Webhook-URL nicht erreichbar |
| Fail erzeugt eine zweite Zeile | n8n schreibt noch parallel; nur der Payments-Provider darf dieses Journal füllen |
| Clearing bleibt nach Payout offen | Gebührenzeile fehlt oder Bankeingang wurde zusätzlich ins GC-Journal importiert |
| ZEN pull leer | Nur IN_PROGRESS im Zeitraum, oder falsche Account-UUID/IBAN |
| Jede Zeile doppelt | n8n läuft noch parallel, oder einmal Datei **und** Online für denselben Feed |
| Jeeves-Beträge positiv statt Aufwand | Datei hat bereits Minusbeträge und wurde zusätzlich invertiert — CSV-Header `Type` prüfen oder eine Zeile zum Nachstellen schicken |
| GoCardless Redirect-Fehler | BAD-Consent abgelaufen oder neue Registrierung gesperrt → CAMT/Ponto |

Keine Zeilen von Hand im Journal löschen, wenn die `unique_import_id`
noch gilt — ein erneuter Pull legt sie sonst nicht neu an. Stattdessen
die Zeile stornieren bzw. den Auszug zurücksetzen und den Zeitraum
bewusst neu pullen.
