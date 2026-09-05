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
2. **Stripe**
   - [dashboard.stripe.com](https://dashboard.stripe.com) → Entwickler → API-Keys
   - Restricted Key (`rk_live_…`) mit Read auf Charges, Customers,
     Balance Transactions. Oder der Secret Key (`sk_live_…`).
   - Webhook-Secret (`whsec_…`) erst, nachdem die URL im Dashboard
     angelegt ist (dieser Key braucht kein `webhook_write`)
3. **ZEN.COM**
   - Bei ZEN einen **Transfers-API-Key** beantragen (nicht den Terminal-Key
     aus my.zen.com Payments)
   - Wallet-IBAN und Währung notieren
   - Optional: Account-UUID, falls du mehrere Wallets hast
4. **GoCardless Payments**
   - Developers → Access Token (Live)
   - Webhook-Endpoint anlegen: `https://DEINE-ODOO/gocardless/payments/webhook`
   - Endpoint-Secret notieren
   - Events: payments (alle), payouts (paid, bounced), refunds
5. **Jeeves**
   - Eine aktuelle Activity-CSV exportieren (*Activity and Exports*)
   - Spalten prüfen: Transaction ID, Posted Date, Merchant, Amount, Status
6. **Bank** (falls kein BAD)
   - Einen CAMT.053 oder CSV-Auszug der Hausbank bereitlegen

---

## Schritt 2 — Module auf den Server legen

Die Varianten (Debian, Docker, Odoo.sh) stehen in [INSTALL.md](INSTALL.md).
Kurz auf dem Host:

```bash
cd /mnt/extra-addons    # dein bestehender Extra-Addons-Pfad

git clone --branch 19.0 --depth 1 https://github.com/OCA/account-reconcile
git clone --branch 19.0 --depth 1 https://github.com/OCA/bank-statement-import
git clone --branch cursor/community-banking-stack-f606 https://github.com/xxxchris0815/odoo_banking
```

`addons_path` um die drei Repo-Wurzeln **ergänzen** (bestehende Einträge
lassen):

```ini
addons_path = /mnt/extra-addons,/mnt/extra-addons/commission,/mnt/extra-addons/account-reconcile,/mnt/extra-addons/bank-statement-import,/mnt/extra-addons/odoo_banking/addons
```

Odoo neu starten, Apps-Liste aktualisieren.

---

## Schritt 3 — Module in Odoo installieren

1. Entwicklermodus an, Apps → Apps-Liste aktualisieren.
2. **Community Banking Stack** (`banking_community`) installieren — zieht
   die Pflichtmodule mit. Optional zusätzlich `account_statement_import_camt`.
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
| Stripe EUR | 1212 | 1812 | Bank / flüssige Mittel |
| ZEN EUR | 1215 | 1815 | Bank / flüssige Mittel |
| Hausbank | 1200 | 1800 | Bank |
| Jeeves Credit | 1665 | 3610 | Verbindlichkeit |
| Jeeves Cash (Prepaid) | 1218 | 1818 | Bank / flüssige Mittel |
| PayPal-Gebühren | 4970 | 6855 | Aufwand |
| Stripe-Gebühren | 4971 | 6855 | Aufwand |
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
| Stripe EUR | STRP | 1212 / 1812 |
| ZEN EUR | ZEN | 1215 / 1815 |
| Jeeves Credit EUR | JCRD | 1665 / 3610 |
| Jeeves Cash EUR | JCSH | 1218 / 1818 |

Zweite Währung = zweites Journal (`PPUSD`, `ZENUSD`, …).

---

## Schritt 6 — Provider am Journal koppeln

### 6a PayPal

Das ist **Transaction Search** aus diesem Repo (Provider **PayPal**),
nicht zwingend das OCA-Modul *PayPal.com*. Wenn beide installiert sind:
**PayPal** nehmen.

#### A) Keys bei PayPal holen

1. [developer.paypal.com](https://developer.paypal.com) → einloggen.
2. **Apps & Credentials** → Schalter auf **Live** (nicht Sandbox).
3. REST-App anlegen oder die vorhandene öffnen.
4. **Client ID** und **Secret** kopieren.
5. Unter **Features** nur **Transaction Search** aktiv lassen, speichern.

Richtige Module (Apps, Filter *Apps* aus):

| Anzeigename | Technischer Name | Version |
| --- | --- | --- |
| Community Banking Stack | `banking_community` | **19.0.1.9.0** |
| PayPal Bank Feed (Expect Magic) | `account_statement_import_online_paypal_reporting` | **19.0.1.7.0** |
| Stripe Bank Feed (Expect Magic) | `account_statement_import_online_stripe_reporting` | **19.0.1.6.0** |
| Online Bank Statements: ZEN.COM | `account_statement_import_online_zen` | **19.0.1.13.0** |
| Online Bank Statements: GoCardless Payments | `account_statement_import_online_gocardless_payments` | **19.0.1.12.0** |
| Bank Statement Import: Jeeves CSV | `account_statement_import_jeeves` | **19.0.1.11.0** |

Erscheint das PayPal-Modul nicht: Filter **Apps** in der App-Liste
ausmachen (sonst sieht man nur `application=True`). Danach
Apps-Liste aktualisieren. Liegt der Ordner nicht auf der Platte,
kennt Odoo das Modul nicht:

```bash
ls /opt/odoo/extra-addons/odoo_banking/addons/account_statement_import_online_paypal_reporting/__manifest__.py
docker exec odoo_app ls /opt/odoo/extra-addons/odoo_banking/addons/account_statement_import_online_paypal_reporting/__manifest__.py
```

Ohne diese Datei: `git pull` im Repo. Neue Felder an Kontakt/Journal
nicht nur mit `docker restart` laden — das ergibt 500
(`column res_partner.… does not exist`). Erst upgraden, dann starten:

```bash
cd /opt/odoo/extra-addons/odoo_banking
git pull origin cursor/community-banking-stack-f606
docker exec odoo_app /entrypoint.sh odoo \
  -u account_statement_import_jeeves,banking_community,account_statement_import_online_paypal_reporting,account_statement_import_online_stripe_reporting,account_statement_import_online_gocardless_payments,account_statement_import_online_zen \
  -d db_odoo --stop-after-init --no-http
docker restart odoo_app
```

Nicht das OCA-Modul `account_statement_import_online_paypal` (Service
*PayPal.com*, Autor CorporateHub/OCA) — das legt die zweite
Credentials-Gruppe. Deinstallieren.

Sandbox-Keys funktionieren nicht gegen die Live-API. Umgekehrt auch nicht.

#### B) Journal und Provider in Odoo

1. Journal *PayPal EUR* bearbeiten (Typ Bank, eine Währung).
2. Bank Feeds = **Online (OCA)**.
3. Service **PayPal** — nicht GoCardless und nicht ein zweites
   OCA-*PayPal.com*, falls das noch in der Liste steht.
4. Speichern, den Provider öffnen:
   - **Client ID** = Client ID aus Schritt A4
   - **Secret** = Secret aus Schritt A4
   - API Base leer = Live (`https://api.paypal.com`)
   - Sandbox nur mit Sandbox-Keys:
     `https://api.sandbox.paypal.com`
   - Intervall z. B. 1 Stunde
   - **Webhook URL** kopieren (ein Token pro PayPal-Konto)
5. Speichern.

#### Webhook (mehrere PayPal-Konten)

Jedes Journal / jeder Provider hat eine **eigene** URL:

`https://DEINE-DOMAIN/paypal/webhook/<token>`

PayPal akzeptiert nur HTTPS. Die angezeigte URL wird immer auf
`https://` ohne internen Odoo-Port (`:8069`) umgeschrieben. Wenn der
Hostname falsch ist: Einstellungen → Technisch → Systemparameter
`web.base.url` = `https://DEINE-DOMAIN` (ohne Port).

Konto A → URL von Provider A, Konto B → URL von Provider B.
Dieselbe URL für zwei Merchant-Accounts nicht verwenden.

1. Provider speichern, **Webhook URL** kopieren.
2. Entweder **Register webhook** klicken (schreibt die Webhook-ID
   automatisch) oder in [developer.paypal.com](https://developer.paypal.com)
   → die Live-App → Webhooks → URL eintragen.
   Events: Payment sale/capture completed, refunded, reversed;
   Dispute created.
3. Die **Webhook ID** (`WH-…` / `0EH…`) ins Provider-Feld, falls du
   manuell eingetragen hast.

Ohne Webhook-ID akzeptiert Odoo den Aufruf nur über das URL-Token.
Mit ID prüft Odoo die PayPal-Signatur. Der Webhook zieht die letzten
drei Tage nach (wie ein kurzer Pull).

#### C) Testen

1. Provider → **Pull Online Bank Statement**.
   Die Uhrzeit im Wizard kommt von OCA; bei täglichen Auszügen zählt
   der Kalendertag.
2. **Show Transaction Data** zeigt nur **neue** Zeilen. Schon
   importierte IDs kommen als `[]`.
3. Erwartung an den Zeilen:
   - Eingang: `[paid] Kundenname — Artikel` und daneben
     `[fee] PayPal — TXN…`
   - Auszahlung auf die Hausbank: `[paid] Withdrawal — …` (minus)
   - Erstattung: eigene Zeile minus, Gebühr oft plus
   - Partner: zuerst gespeicherte PayPal-Account-ID am Kontakt, sonst
     eindeutige Zahler-E-Mail. Nach dem ersten E-Mail-Treffer schreibt
     Odoo die ID auf den Kontakt.
4. Denselben Zeitraum ein zweites Mal pullen: keine Duplikate
   (`pp:tx:{transaction_id}`).

Auszahlungen nicht per n8n auf dieses Journal schreiben. Die Hausbank
bekommt den Eingang aus ihrem eigenen Auszug; Abstimmung über Geldtransit.

### 6e Stripe

Nicht das OCA-Modul *Online Bank Statements: Stripe*
(`account_statement_import_online_stripe`, Autor OCA). Unseres heißt
**Stripe Bank Feed (Expect Magic)**, technisch
`account_statement_import_online_stripe_reporting`.

Provider **Stripe**. Restricted Key reicht (Read auf Balance
Transactions, Charges, Customers). `webhook_write` ist nicht nötig.

1. Journal *Stripe EUR*, Bank Feeds = **Online (OCA)** → Service **Stripe**.
2. **API key** = `rk_live_…` oder `sk_live_…`. Speichern.
3. **Webhook URL** kopieren (`https://DEINE-DOMAIN/stripe/webhook/<token>`).
4. Stripe Dashboard → Entwickler → Webhooks → Add endpoint.
   Genau diese HTTPS-URL. Events: `charge.succeeded`, `charge.refunded`,
   `payout.paid`, `payment_intent.succeeded`.
5. Signing secret (`whsec_…`) nach Odoo ins Feld **Webhook signing secret**.

Mehrere Stripe-Accounts = mehrere Provider, jeder mit eigenem Token.
Der Restricted Key kann den Hook nicht selbst anlegen (403) — nur
manuell im Dashboard.

Erwartung: `[paid] Kundenname — Produkt`, `[fee] Stripe — txn_…`,
`[paid] Payout — po_…`. `unique_import_id` = `st:txn:…`.
Partner: zuerst gespeicherte Stripe-Kunden-ID (`cus_…`) am Kontakt, sonst
eindeutige E-Mail. Nach dem ersten E-Mail-Treffer schreibt Odoo die ID.

Saldo: PayPal schickt `available_balance` auf jeder Transaktion. Stripe
nicht — `/v1/balance` ist nur das Guthaben *jetzt*. Der Feed rekonstruiert
das Wallet zum Auszugsende: aktuelles Guthaben (available + pending)
minus Netto aller späteren Balance Transactions. GoCardless ist Clearing,
kein Wallet, und setzt deshalb keine Salden.

### 6b ZEN.COM

1. Journal *ZEN EUR*, IBAN muss zur Wallet passen.
2. Bank Feeds = **Online (OCA)** → Provider **ZEN.COM**.
3. Provider öffnen:
   - Password = Transfers-API-Key (nicht der Terminal-Key aus Payments)
   - Username = Account-UUID (leer lassen, wenn die IBAN eindeutig ist)
   - API Base leer = Produktion (`https://api-services.zen.com`)
   - **mTLS client certificate** = ganzen PEM-Block reinkopieren
     (`-----BEGIN CERTIFICATE-----` … `-----END CERTIFICATE-----`)
   - **mTLS private key** = ganzen PEM-Block reinkopieren
     (`-----BEGIN PRIVATE KEY-----` oder `RSA PRIVATE KEY` … `-----END …-----`)
     Nicht ins Feld API Key. `.p12`/`.pfx` zuerst nach PEM wandeln.
   - **mTLS CA chain** nur wenn ZEN eine CA-Datei mitgeliefert hat
   - **Private key passphrase** nur wenn der Key verschlüsselt ist
   - **Account UUID** = `accountId` aus der Notification (z. B. `58d85a6c-…`)
   - **Webhook URL** in Odoo kopieren
     (`https://DEINE-DOMAIN/zen/webhook/<token>`).
4. Speichern. Ohne Zertifikat + Key kommt kein Request durch (mTLS).

ZEN erlaubt oft nur **eine** Notification-URL. Dann bleibt
`https://automation.orgasmic.live/webhook/zen-webhook` bei ZEN, und n8n
reicht nur durch — **keine** Payment-Details und **keine** Journalzeilen
mehr in n8n (sonst Duplikate).

n8n, zwei Nodes:

1. **Webhook** (POST, Production URL = die bei ZEN). Respond = *Immediately*,
   Antwort `ok` / 200.
2. **HTTP Request** direkt danach:
   - Method `POST`
   - URL = die Webhook-URL aus dem Odoo-Provider
   - Header `Content-Type: application/json`
   - Body = JSON, Expression `{{ $json.body }}`
     (das ist der ZEN-Stub: `paymentId`, `accountId`, `transactionStatus`)
   - Timeout 30s, Fehler nicht den Webhook-200 an ZEN verderben

Nicht den ganzen n8n-Envelope (`webhookUrl`, `executionMode`) schicken,
nicht mTLS/Bearer am Odoo-Hook (nur der Token in der URL). Weitere
Empfänger = weitere HTTP-Request-Nodes parallel.

Der Stub enthält nur `paymentId` / `accountId` / `transactionStatus`.
Odoo holt danach `GET /payments/v1.0/{paymentId}`. Nur `SETTLED` wird
gebucht. `unique_import_id` = `zen:pay:{id}`. Gebühren > 0 werden eigene
Zeilen (`zen:pay:{id}:fee`).

Webhook prüfen:

1. GET auf die Odoo-URL im Browser — Text `ok`.
2. n8n HTTP-Node: Status **200**, Response-Body `ok`. 404 = Token falsch,
   500 = mTLS/API beim Nachladen der Zahlung.
3. Container-Log: `docker logs odoo_app 2>&1 | grep -i zen`
   Erfolg: `ZEN webhook payment=…` und `ZEN webhook booked payment=…`.
4. Journal *ZEN EUR*: Zeile `Partner — Verwendungszweck` (ohne `[paid]`),
   `unique_import_id` `zen:pay:{id}`. Gegenpartei-IBAN steht auf der Zeile
   und in der Narration (`iban=…`). Zuerst IBAN unter *Kontakte →
   Bankkonten*, sonst eindeutiger Name — dann legt Odoo die IBAN am
   Kontakt an.
   Zweiter Hook dieselbe ID: 200 und `already on the journal` — kein Duplikat.

### 6c GoCardless Payments — Daten eintragen

#### A) Keys bei GoCardless holen

1. Einloggen unter [manage.gocardless.com](https://manage.gocardless.com)
   (Tests: [manage-sandbox.gocardless.com](https://manage-sandbox.gocardless.com)).
2. **Developers** → **Create access token** → Name z. B. `Odoo` →
   Read-Write → Token **sofort kopieren** (nur einmal sichtbar).
3. **Developers** → **Webhooks** → **Add endpoint**
   - URL: `https://DEINE-DOMAIN/gocardless/payments/webhook`
   - Events: `payments`, `payouts`, `refunds`
   - Endpoint-Secret kopieren.

#### B) Journal und Provider in Odoo

1. App **Fakturierung** (Community) bzw. **Buchhaltung**.
2. Benutzer muss **Vollständige Buchhaltungsfunktionen anzeigen** haben,
   sonst fehlt das Menü.
3. **Konfiguration → Journale** → Journal `GC` öffnen (Typ Bank,
   Buchungskonto = GoCardless-Clearing).
4. Feld **Bankauszüge / Bank Feeds** = **Online (OCA)**.
5. Im Provider-Formular das Feld **Service** auf **GoCardless Payments**
   stellen — nicht **PayPal** und nicht das OCA-„GoCardless“ (Open Banking).
   Client ID / Secret gehören nur zu PayPal; die erscheinen, wenn Service
   falsch auf PayPal steht.
6. Speichern. Odoo legt den Provider an.
7. Den Provider-Namen anklicken (oder **Konfiguration → Online Bank
   Statement Providers** → den Eintrag zum Journal `GC` öffnen).
8. Im Kasten **GoCardless Payments**:
   - **Access Token** = Token aus Schritt A2
   - **Webhook-Secret** = Secret aus Schritt A3
   - **API-Adresse** leer = Live; Sandbox:
     `https://api-sandbox.gocardless.com`
9. Speichern.

#### C) Testen

1. Provider-Formular → **Pull Online Bank Statement**.
   Die Uhrzeit im Wizard kommt von OCA; bei täglichen Auszügen zählt
   nur der Kalendertag (Mitternacht bis Mitternacht). 22:28 ist egal.
2. **Show Transaction Data** (Debug) zeigt nur **neue** Zeilen. Schon
   importierte IDs kommen als `[]` — das heißt nicht „nichts bei
   GoCardless“, sondern „schon im Journal“.
3. Es werden nur Einzüge mit Charge-Datum im Zeitraum importiert, plus
   Einzüge die zu einem Payout in diesem Zeitraum gehören. Zukünftige
   Raten (`pending_submission` im Oktober/November) bleiben draußen,
   bis ihr bis zu deren Einzugsdatum pullt.
4. Oder einen Sandbox-Einzug auslösen: im Journal `GC` erscheint
   `[submitted] Kundenname — Referenz` / 0, nach Confirm dieselbe Zeile mit +.

Payouts nicht per n8n auf dieses Journal schreiben. Die Hausbank bekommt
den Eingang aus ihrem eigenen Auszug.

### 6d Jeeves

Zwei Wege, **einen** pro Zeitraum, nicht beide (sonst Duplikate):

**A) Täglicher Pull über MCP** (wie n8n `list_transactions`)

1. Journal *Jeeves Cash EUR* (bzw. USD), Bank Feeds = **Online (OCA)** →
   Service **Jeeves**.
2. **MCP API key** = Key aus Jeeves *Settings → Product Settings*
   (MCP Integration), per **Bearer**. Nicht in n8n-Notes committen.
3. **Account id** = `productAccountId` der Cash-Währung (EUR/USD/GBP).
   Leer lassen: Odoo ruft `list_accounts` und nimmt das aktive Cash-Konto
   zur Journal-Währung.
4. Speichern. **Pull Online Bank Statement** für ein paar Tage testen.
5. Der OCA-Cron holt danach täglich. n8n darf diese Zeilen **nicht**
   zusätzlich ins Journal schreiben. Für Auszüge: `list_accounts` /
   `list_transactions`. Zum Rechnungsabgleich zusätzlich
   `list_billpay_invoices`.

**Rechnungen ↔ Jeeves-Zahlungen**

- MCP-Pull und Activity-CSV hängen die Odoo-Belegnummer an die
  Auszugszeile (`BILL/…`, `PROV…`, `RE4583`), wenn Betrag + Lieferant
  zu einer Jeeves-Bill-Pay-Rechnung passen.
- Auf der Lieferantenrechnung: *Sync Jeeves invoice* schreibt
  `jeeves_invoice_id`, Status und `JPP…`.
- Zahlen aus Odoo: unter **Lieferantenrechnungen** 1–n Zeilen
  markieren → **Jeeves Bulk-CSV** (Listen-Button) oder Aktion
  **Jeeves Bulk-CSV herunterladen**. Es öffnet sich ein Dialog mit der
  Datei `Bulk-Payments-YYYY-MM-DD.csv` — dieselbe Spaltenvorlage wie
  in der Jeeves-Web-UI. Datei speichern und in Jeeves unter Bulk
  Payments importieren. Kein Kontoauszug, nicht `file_upload`.
- `file_upload` ist nur für Spesenbelege (PDF/JPEG/PNG/GIF, 10 MB) und
  eine `uploadId` für `add_reimbursement`. Odoo ruft das nicht auf.

**Lieferanten in Jeeves** (am Kontakt, Button *Jeeves* / *Create / update
in Jeeves*):

1. Kontakt muss Name, E-Mail, Telefon, Straße, PLZ, Ort, Land und eine
   IBAN (Bankverbindung) haben. Telefon im Format `+49 151…`. Ohne
   Straße/PLZ/Ort/Telefon legt Jeeves den Vendor nicht an — der Assistent
   blockt das Schreiben und listet die fehlenden Felder.
2. Bankland kommt aus der IBAN (`LT…` → Litauen), nicht aus dem
   Kontaktland. Der graue Text `+49 151…` ist nur ein Platzhalter.
3. Der Assistent sucht per `get_vendor` / `list_vendors` nach Vendor-ID,
   E-Mail oder Name. **Von Jeeves laden** füllt nur das Formular.
   **Nach Odoo schreiben** legt Telefon, Adresse und Bankland auf den
   Kontakt. **Write to Jeeves** sendet die Odoo-Felder nach Jeeves.
   Maskierte Kontonummern (`****3012`) überschreiben keine IBAN.
4. Die Jeeves-Vendor-ID landet auf dem Kontakt. *Link ID only* schreibt
   nur die ID, ohne Jeeves zu ändern.
5. Karten anlegen oder Rechnungen zahlen tut Odoo **nicht**.

Live-`list_transactions` paginiert (max. 100/Seite) und filtert
`settled`. Die Text-Antwort hat oft keine Unique ID — Odoo bildet dann
`jeeves:mcp:{fingerprint}` aus Zeitpunkt, Betrag und Gegenpartei.
Kommt `id` / `transactionId`, wird das genommen. Dieselbe Bewegung per
CSV importieren erzeugt trotzdem eine zweite Zeile (CSV nutzt Unique ID
ohne Prefix).

**B) Datei** — Schritt 9, Bank Feeds nicht auf Online. Import OCA.

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

1. In Jeeves: Activity and Exports → **ein Konto** (EUR oder USD) +
   Zeitraum → CSV. Live-Header: `Unique ID`, `Posted At UTC`,
   `Credit or Debit`, `Amount (origin currency)`, `Payee`, `Vendor Email`.
2. Pending-Zeilen dürfen in der Datei bleiben, der Parser wirft sie weg.
3. In Odoo: Journal *Jeeves Cash EUR* bzw. *Jeeves Cash USD* → Auszug
   importieren. Die Datei-Währung muss zum Journal passen.
4. Dieselbe Datei nicht in ein anderes Journal laden.
5. Dieselbe Datei ein zweites Mal importieren: keine neuen Zeilen
   (`Unique ID`).

Partner: zuerst gespeicherte Jeeves-Vendor-ID am Kontakt, sonst
eindeutige Vendor-E-Mail, sonst eindeutiger Payee-Name. Nach dem ersten
E-Mail-Treffer schreibt Odoo die Vendor-ID. IDs nicht von Hand pflegen.

Label: `Payee — Rechnungsnummer/Memo` (ohne `[paid]`). Einzahlungen ohne
Payee nutzen die Payment Description (`STRIPE`, `INV/2026/00036`).

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

### Stripe-Gebühr

- Label enthält `fee` / `Stripe`
- Gegenkonto: Stripe-Gebührenaufwand
- Partner leer lassen

### Geldtransit (interne Transfers)

- Betrag gleich, Gegenjournal das Zielkonto, Toleranz ±1 Tag
- Gegenkonto: Geldtransit aus Schritt 4
- Für PayPal→Bank, ZEN→Bank, Jeeves-Settlement, **GoCardless-Payout→Bank**
  je eine Regel oder eine gemeinsame mit Betragsmatch

GoCardless-Payout: Zeile `[payout paid] Bank transfer POxxx` (−Netto)
gegen den Bankeingang (+Netto). Gebührenzeile gegen den Gebührenaufwand.
Nicht gegen Kundenrechnungen — Kundendaten stehen nur auf den `PM…`-Zeilen.

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
2. Im Journal `GC` steht die **Einzugszeile**
   `[confirmed] Kundenname — INV-…` (`PMxxx`) mit +Betrag, Partner und
   E-Mail/IBAN in der Notiz. Das ist die Zeile gegen die Rechnung.
   Die Zeile hängt am **Einzugsdatum** (charge date), nicht am Payout-Tag.
   Partner-Match: zuerst gespeicherte GoCardless-Kunden-ID (`CUxxx` am
   Kontakt), sonst eindeutige E-Mail, sonst IBAN. Nach dem ersten
   E-Mail-/IBAN-Treffer schreibt Odoo die `CUxxx` auf den Kontakt —
   IDs nicht von Hand pflegen. Nur Name allein speichert die ID nicht.
   Im Abstimmen-Bildschirm alle offenen Zeilen des Journals anzeigen,
   nicht nur das Statement vom Auszahlungstag.
3. Dashboard → GC → **Abstimmen** → offene Rechnung desselben Partners.
   Odoo matcht über Partner, Referenz und Betrag.
4. Rechnung = bezahlt, Clearing = belastet. Ein Buchungssatz.

Die Zeile `[payout paid] Bank transfer POxxx` ist die **Sammelauszahlung
an die Hausbank**. Die hat keine Kundennamen und darf **nicht** gegen
eine Rechnung. Die stimmt ihr gegen den Bankeingang (Geldtransit) plus
die Gebührenzeile.

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
- [ ] Stripe: 7-Tage-Pull ok, zweiter Pull ohne Duplikate
- [ ] ZEN: nur SETTLED, IBAN/UUID stimmt, Vorzeichen richtig
- [ ] GoCardless: Einzug sichtbar, Fail ändert dieselbe Zeile auf 0,
      Payout + Gebühr setzen Clearing auf 0
- [ ] Rechnung nur über GC-Clearing bezahlt, Payout nicht nochmal gegen
      dieselbe Rechnung
- [ ] Bank: CAMT/CSV oder anderer Bankfeed, nicht GoCardless BAD
- [ ] Jeeves: eine CSV, Pending weg, Re-Import ohne Duplikate
- [ ] PayPal-Auszahlung erscheint auf PayPal **und** Bank und geht über Geldtransit
- [ ] Stripe-Payout erscheint auf Stripe **und** Bank und geht über Geldtransit
- [ ] Abstimmungmodell Gebühren und Transfers greifen
- [ ] n8n schreibt keine Journalzeilen mehr

---

## Wenn etwas schiefgeht

| Symptom | Typische Ursache |
| --- | --- |
| Ganze Odoo-Seite 500 nach `git pull` + Restart | Neue Spalte an `res.partner` (PayPal/Stripe/GC/Jeeves-ID) ohne Modul-Upgrade. Log: `column res_partner.… does not exist`. Fix: `odoo -u account_statement_import_jeeves,… -d db_odoo --stop-after-init --no-http`, dann `docker restart odoo_app`. Nicht die Apps-UI, solange /web tot ist. |
| Kein Menü „Online Bank Statement Providers“ | Gruppe volle Buchhaltung fehlt, oder `account_statement_import_online` nicht installiert |
| Provider-Feld am Journal fehlt | Journal-Typ ist nicht Bank |
| PayPal pull leer | Sandbox-Key, oder Zeitraum älter als 3 Jahre |
| PayPal-Formular zeigt Client ID zweimal | OCA-Modul `account_statement_import_online_paypal` ist noch aktiv, oder Stack nicht auf **19.0.1.6.0**. Richtig: `account_statement_import_online_paypal_reporting` **19.0.1.4.0** (Expect Magic). OCA-PayPal deinstallieren, Stack + PayPal-Reporting upgraden. Im Provider-Formular steht die Version unter **Module version**. |
| PayPal lehnt die Webhook-URL ab (http) | Odoo speichert oft `http://…:8069`. Ab 19.0.1.4.0 wird daraus `https://deine-domain` ohne Port. Zusätzlich Einstellungen → Technische Parameter `web.base.url` auf `https://erp.…` setzen. |
| Stripe: running balance matches not ending | Live-`/v1/balance` ist oft 0, weil das Payout später kam. Ab **19.0.1.3.0** ist der Endsaldo das Stripe-Wallet *an dem Tag* (jetzt minus spätere Nets). Bestehenden Auszug `BNK4/…`: Endsaldo auf den Computed Balance setzen **oder** Auszug samt Zeilen löschen und den Tag neu pullen. Ein zweiter Pull allein ändert den alten Endsaldo nicht. |
| ZEN 403 Invalid authentication credentials | Header muss `Authorization: Bearer <apiKey>` sein (wie n8n), plus mTLS. Ab **19.0.1.5.0**. Key ohne das Wort Bearer ins Feld legen. |
| ZEN 500 INTERNAL_SERVER_ERROR | Live-History ist `GET /payments/v1.0?accountId=&bookedAtFrom=&bookedAtTo=` (ohne `/history`). Antwort ist `[{data, meta}]`, nicht das Objekt aus der Doku. Details: `GET /payments/v1.0/{uuid}`. |
| ZEN-Webhook bucht nichts | n8n-URL steht noch in ZEN Notifications. Auf `https://DEINE-DOMAIN/zen/webhook/<token>` umstellen. Account UUID am Provider muss zum `accountId` passen. |
| GoCardless-Einzug fehlt | Access Token Live/Sandbox verdreht, oder Webhook-URL nicht erreichbar |
| Fail erzeugt eine zweite Zeile | n8n schreibt noch parallel; nur der Payments-Provider darf dieses Journal füllen |
| Clearing bleibt nach Payout offen | Gebührenzeile fehlt oder Bankeingang wurde zusätzlich ins GC-Journal importiert |
| ZEN-Zeile heißt noch `[paid] Name — INV/…` | Altes Label aus dem Status `SETTLED→paid`. Ab **19.0.1.12.0** nur noch `Name — INV/…`. Bestehende Zeilen bleiben (gleiche `unique_import_id`). Zeile editieren oder Auszug löschen und den Tag neu pullen. |
| ZEN-Zeile ohne Partner | IBAN unter *Kontakte → Bankkonten*, oder eindeutiger Name (dann speichert Odoo die IBAN). PayPal/Stripe: gespeicherte Account-/Kunden-ID oder eindeutige E-Mail. |
| ZEN pull leer | Nur IN_PROGRESS im Zeitraum, oder falsche Account-UUID/IBAN |
| Jede Zeile doppelt | n8n läuft noch parallel, oder einmal Datei **und** Online für denselben Feed |
| Jeeves-Beträge positiv statt Aufwand | Live-Export hat immer Plusbeträge; das Vorzeichen steht in `Credit or Debit`. Modul **19.0.1.1.0** upgraden. Ältere Dateien: Spalte `Type` prüfen. |
| Jeeves-CSV wird nicht erkannt | Live-Header ab 19.0.1.1.0. Datei muss `Unique ID` + `Posted At UTC` + `Amount (origin currency)` haben, oder die alten Spalten `Transaction ID` / `Posted Date` / `Amount`. |
| Jeeves-MCP-Zeilen doppelt zur CSV | MCP-Fingerprint ≠ CSV-Unique-ID. Entweder täglicher Provider **oder** Import OCA für denselben Zeitraum. n8n nicht parallel ins Journal schreiben. |
| GoCardless Redirect-Fehler | BAD-Consent abgelaufen oder neue Registrierung gesperrt → CAMT/Ponto |

Keine Zeilen von Hand im Journal löschen, wenn die `unique_import_id`
noch gilt — ein erneuter Pull legt sie sonst nicht neu an. Stattdessen
die Zeile stornieren bzw. den Auszug zurücksetzen und den Zeitraum
bewusst neu pullen.
