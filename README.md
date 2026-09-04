# Odoo 19 Community Banking

Sauberer Ersatz für die Odoo-Cloud-Kontenkopplung plus n8n-Abgleich.
Ziel: jede Bewegung landet als `account.bank.statement.line` in der
Community-Buchhaltung und wird dort abgestimmt — nicht in n8n.

**Installation auf dem Server:** [INSTALL.md](INSTALL.md)

**Danach einrichten:** [SETUP.md](SETUP.md) — Journals, Keys, erster Pull.

## Empfehlung in einem Satz

**OCA Online Bank Statements als einzige Import-Pipeline, n8n nur noch als
optionaler Auslöser/Dateitransport, nie als Buchungslogik.**

Odoo Community hat seit v14 keinen Statement-Import und keinen
Abstimmungsbildschirm mehr. Beides liegt bei der OCA. PayPal und GoCardless
Bank Account Data gibt es dort bereits für 19.0. ZEN.COM und Jeeves baut
dieses Repo als dünne Provider auf demselben Framework.

## Zielarchitektur

```
PayPal API          ──►  OCA account_statement_import_online_paypal
GoCardless Einzüge  ──►  account_statement_import_online_gocardless_payments
ZEN.COM Transfers   ──►  account_statement_import_online_zen   (dieses Repo)
Jeeves CSV          ──►  account_statement_import_jeeves       (dieses Repo)
Bank-CAMT/CSV       ──►  OCA account_statement_import_camt / sheet_file
                              │
                              ▼
                   account.bank.statement.line
                   unique_import_id = Provider-Transaktions-ID
                              │
                              ▼
                   OCA account_reconcile_oca
                   + Abstimmungmodelle (Gebühren, Intercompany, Transfers)
```

Ein Journal **pro Konto und Währung**. Kreditkarten (Jeeves Credit) auf ein
Verbindlichkeitskonto, Prepaid/Cash und Wallets auf ein Geldkonto.

## Was du aus der Cloud / aus n8n nicht mitnehmen solltest

| Bisher | In Community |
| --- | --- |
| Odoo Online Bank Sync | OCA `account_statement_import_online` + Cron |
| n8n mapped Felder und postet Journalzeilen | Provider erzeugen Statement-Zeilen, Odoo bucht |
| n8n Deduplizierung | `unique_import_id` (PayPal/ZEN/GoCardless-ID, Jeeves Transaction ID) |
| n8n Partner-Matching | Abstimmungmodelle + Partner-IBAN |
| n8n Fehler-Retries | Provider-Chatter + Scheduled Pull |

n8n darf bleiben für:

- Jeeves-CSV aus dem Postfach/S3 auf den Odoo-Import-Wizard (JSON-RPC)
- Alarm, wenn ein Provider im Chatter fehlschlägt
- nicht für Kontenzuordnung, Betragsvorzeichen oder Steuern

## OCA-Module (19.0), die du brauchst

Aus [OCA/account-reconcile](https://github.com/OCA/account-reconcile/tree/19.0):

- `account_statement_base`
- `account_reconcile_oca`
- `account_reconcile_model_oca`

Aus [OCA/bank-statement-import](https://github.com/OCA/bank-statement-import/tree/19.0):

- `account_statement_import_base`
- `account_statement_import_file`
- `account_statement_import_online`
- `account_statement_import_online_paypal`
- `account_statement_import_online_gocardless` (optional, nur bei aktivem BAD-Zugang)
- `account_statement_import_sheet_file` (Fallback für beliebige CSVs)
- optional `account_statement_import_camt` für echte Bank-CAMT.053

Dieses Repo:

- `account_statement_import_online_zen`
- `account_statement_import_online_gocardless_payments`
- `account_statement_import_jeeves`
- `banking_community` — Meta-Modul, installiert den Stack in einem Schritt

Benutzergruppe: **Vollständige Buchhaltungsfunktionen anzeigen**.

## Provider im Detail

### PayPal — fertiges OCA-Modul

1. PayPal Developer App (Live): Client ID + Secret.
2. Bankkonto + Journal `PayPal EUR` (bzw. eine Währung pro Journal).
3. Journal → Bank Feeds = *Online (OCA)* → Provider *PayPal.com*.
4. Client ID / Secret setzen, historischen Pull starten, danach Cron.

Nur gebuchte Transaktionen der letzten drei Jahre. Ältere Historie per
PayPal-CSV und `account_statement_import_sheet_file`.

### GoCardless Payments — Clearing-Journal (dieses Repo)

Du ziehst Lastschriften ein. Dafür ein eigenes Journal `GC EUR` auf einem
**abstimmbaren Clearing-Konto** (nicht die Hausbank):

| Ereignis | Zeile | Betrag |
| --- | --- | --- |
| Einzug submitted / pending | sichtbar, Status im Text | 0 |
| Einzug confirmed / paid_out | dieselbe `unique_import_id` | +Betrag |
| failed / cancelled / charged_back | dieselbe Zeile, Status nachgezogen | 0 |
| Payout an die Hausbank | eigene Zeile | −Netto |
| GoCardless-Gebühr | eigene Zeile | −Fee |

Clearing steht wieder auf 0. Die Gutschrift auf der Hausbank kommt aus dem
Bankfeed und wird über Geldtransit gegen den Payout abgestimmt.

Rechnungen zahlst du gegen dieses Clearing (Auszug `confirmed` → Rechnung,
oder Zahlung registrieren mit Journal GC). Den Payout nicht noch einmal
als Zahlungseingang auf dieselbe Rechnung buchen. Details: SETUP.md
Schritt 10b.

Webhook `/gocardless/payments/webhook` zieht Fehlschläge sofort nach.
Der Cron holt zusätzlich 90 Tage zurück, falls ein Webhook verloren ging.

Access Token ins Provider-Password, Webhook-Secret ins Passphrase-Feld.
Sandbox: API Base `https://api-sandbox.gocardless.com`.

**Nicht** das OCA-Modul `account_statement_import_online_gocardless`
verwenden — das ist Open Banking (Bank Account Data), nicht Einzüge.

### ZEN.COM — Modul in diesem Repo

Nutzt die Transfers API (`api-services.zen.com`), nicht die Shop-Payments-API.

1. API-Key bei ZEN beantragen (nicht der Terminal-Key aus my.zen.com Payments).
2. Journal `ZEN EUR` mit der Wallet-IBAN.
3. Online Provider *ZEN.COM*:
   - Password = API Key
   - Username = optionale Account-UUID (sonst IBAN-Match)
   - API Base leer = Produktion, sonst `https://api-services.zen-test.com`
4. Es werden nur `SETTLED`-Zahlungen übernommen. `IN_PROGRESS` / `REJECTED`
   bleiben draußen, sonst entstehen Duplikate sobald sie settled sind.

Ohne Transfers-API-Zugang: monatlichen CSV-Kontoauszug aus der ZEN-App
über `account_statement_import_sheet_file` importieren.

### Jeeves — CSV-Parser in diesem Repo

Keine öffentliche Bank-Feed-API. Export aus *Activity and Exports* oder der
Kreditkartenabrechnung.

Der Parser erkennt typische Header (Transaction ID, Posted Date, Merchant,
Amount, Status, …), auch `;` und europäische Zahlen (`-120,00`).

- Status Pending / Authorization wird verworfen
- Einkäufe ohne explizites Vorzeichen werden als Abgang gebucht
- Refunds / Credits bleiben positiv
- `unique_import_id` = Transaction ID

Datei im Journal-Dashboard über den normalen Statement-Import laden.
n8n kann dieselbe Datei per JSON-RPC an `account.statement.import` schieben,
soll die Zeilen aber nicht selbst umbauen.

## Einrichtung

Die Klick-Anleitung steht in [SETUP.md](SETUP.md). Kurz die Reihenfolge:

1. Klären, ob GoCardless Bank Account Data oder nur Merchant-Payouts ist.
2. PayPal-Live-Keys, ZEN-Transfers-Key, optional BAD-Secrets, eine Jeeves-CSV.
3. OCA `19.0` + dieses Repo auf den Addons-Pfad, Odoo neu starten.
4. Module installieren, Gruppe *Vollständige Buchhaltungsfunktionen*.
5. Kontenplan: Geldtransit (abstimmbar), ein Geldkonto pro Wallet/Währung,
   Jeeves Credit als Verbindlichkeit.
6. Ein Journal pro Konto und Währung. GoCardless = Clearing-Journal,
   nicht die Hausbank. Danach Provider am Journal.
7. Zuerst 7 Tage an **einem** Journal pullen, Re-Pull ohne Duplikate, dann
   Historie, dann das nächste Journal.
8. Jeeves nur per Datei-Import, nicht parallel in n8n mappen.
9. Abstimmungmodelle für Gebühren und Geldtransit.
10. Zwei saubere Cron-Läufe, Stichprobe gegen die Portale, dann n8n-Buchung
    abschalten.

## Interne Transfers

Bewegungen zwischen diesen Konten müssen auf **beiden** Journalen
erscheinen und gegeneinander abgestimmt werden, nicht als Aufwand.

Beispiel: PayPal-Auszahlung 1.000 EUR auf das Geschäftskonto

- PayPal-Journal: −1.000, Gegenkonto Interim Transfer
- Bank-Journal (GoCardless/CAMT): +1.000, dasselbe Interim-Konto
- Abstimmungmodell: Referenz / Betrag / ±1 Tag

## Tests ohne Odoo

Die Provider-Logik ist absichtlich Odoo-frei, damit sie ohne Instanz
läuft:

```bash
python3 -m pip install pytest
python3 -m pytest
```

## Addons-Pfad

```
--addons-path=odoo/addons,OCA/account-reconcile,OCA/bank-statement-import,odoo_banking/addons
```
