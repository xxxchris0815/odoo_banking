# Installation auf Odoo 19 Community

Ziel: die Addons liegen auf dem `addons_path`, Odoo kennt sie, du
installierst **ein** Modul (`banking_community`). Danach Journals und
Keys wie in [SETUP.md](SETUP.md).

Voraussetzung: **Odoo 19 Community** (nicht 17/18, nicht Enterprise-only).
Python-Paket `requests` ist in Odoo schon dabei.

---

## 1. Addons-Pfad finden

Auf dem Server:

```bash
# läuft Odoo als Dienst?
ps aux | grep -E 'odoo|openerp'

# typische Konfig
ls /etc/odoo/odoo.conf /opt/odoo/odoo.conf 2>/dev/null
```

In der Datei die Zeile `addons_path` notieren. Alles, was du gleich
klonen, muss **dort als Verzeichnis** stehen — nicht nur das Repo-Root,
sondern bei uns `…/odoo_banking/addons`.

---

## 2. Repos klonen

Lege die drei Quellen **neben** den Core-Addons, nicht nach
`odoo/addons` (das überschreibt Updates).

```bash
sudo -u odoo mkdir -p /opt/odoo/extra
cd /opt/odoo/extra

sudo -u odoo git clone --branch 19.0 --depth 1 \
  https://github.com/OCA/account-reconcile

sudo -u odoo git clone --branch 19.0 --depth 1 \
  https://github.com/OCA/bank-statement-import

sudo -u odoo git clone --branch cursor/community-banking-stack-f606 \
  https://github.com/xxxchris0815/odoo_banking
```

Benutzer `odoo` und Basis `/opt/odoo` anpassen, wenn bei dir anders.

`account-reconcile` und `bank-statement-import` **sind selbst** Addons-Pfade
(jedes Unterverzeichnis ist ein Modul). Bei `odoo_banking` ist der
Addons-Pfad das Unterverzeichnis `addons/`.

---

## 3. `addons_path` erweitern

### Klassische Installation (`odoo.conf`)

Bestehenden `addons_path` **ergänzen**, nicht ersetzen:

```ini
addons_path = /usr/lib/python3/dist-packages/odoo/addons,/opt/odoo/extra/account-reconcile,/opt/odoo/extra/bank-statement-import,/opt/odoo/extra/odoo_banking/addons
```

Debian-Paket oft:

```ini
addons_path = /usr/lib/python3/dist-packages/odoo/addons,/var/lib/odoo/extra/account-reconcile,/var/lib/odoo/extra/bank-statement-import,/var/lib/odoo/extra/odoo_banking/addons
```

Dann:

```bash
sudo systemctl restart odoo
# oder
sudo systemctl restart odoo19
```

### Docker / Compose

Repos auf den Host klonen, ins Container-Volume mounten:

```yaml
services:
  odoo:
    image: odoo:19
    volumes:
      - odoo-data:/var/lib/odoo
      - ./extra/account-reconcile:/mnt/extra-addons/account-reconcile
      - ./extra/bank-statement-import:/mnt/extra-addons/bank-statement-import
      - ./extra/odoo_banking/addons:/mnt/extra-addons/odoo_banking
    environment:
      HOST: db
      USER: odoo
      PASSWORD: odoo
    command: >
      odoo
      --addons-path=/usr/lib/python3/dist-packages/odoo/addons,/mnt/extra-addons/account-reconcile,/mnt/extra-addons/bank-statement-import,/mnt/extra-addons/odoo_banking
```

Container neu starten.

Odoo.sh: Extra-Addons als Git-Submodules im Repo, Branch 19.0, Deploy.

---

## 4. Modul in Odoo installieren

1. Als Admin einloggen.
2. Einstellungen → *Entwicklermodus aktivieren*.
3. Apps → Menü **Apps-Liste aktualisieren** (sonst erscheint das neue Modul nicht).
4. Filter *Apps* entfernen, suchen nach **Community Banking Stack**
   (technisch `banking_community`).
5. Installieren. Odoo zieht automatisch:
   - `account_reconcile_oca`
   - `account_statement_import_online` + PayPal
   - `account_statement_import_online_zen`
   - `account_statement_import_online_gocardless_payments`
   - `account_statement_import_jeeves`
   - Sheet-/File-Import

Oder auf der Konsole (Datenbankname ersetzen, Odoo vorher stoppen oder
`--http-port` frei wählen):

```bash
sudo -u odoo odoo \
  -c /etc/odoo/odoo.conf \
  -d DEINE_DATENBANK \
  -i banking_community \
  --stop-after-init
```

Danach den normalen Odoo-Dienst wieder starten.

---

## 5. Rechte

Einstellungen → Benutzer → dein User:

- **Vollständige Buchhaltungsfunktionen anzeigen** (Show Full Accounting Features)
- neu einloggen

Ohne den Haken fehlen Journals, Provider und der Abstimmungsbildschirm.

---

## 6. Prüfen, ob es sitzt

Fakturierung → Konfiguration:

- *Online Bank Statement Providers* ist sichtbar
- Beim Anlegen eines Providers erscheinen **PayPal.com**, **ZEN.COM**,
  **GoCardless Payments**

Webhook von außen (nur GoCardless):

```
https://DEINE-DOMAIN/gocardless/payments/webhook
```

Die URL muss aus dem Internet erreichbar sein (Proxy, TLS, kein Basic-Auth
vor dieser Route).

---

## Wenn das Modul nicht auftaucht

| Symptom | Ursache |
| --- | --- |
| Suche findet nichts | Apps-Liste nicht aktualisiert, oder Entwicklermodus aus |
| `Module not found: account_reconcile_oca` | OCA-Repo nicht auf dem `addons_path` oder Branch nicht `19.0` |
| `Module not found: account_statement_import_online_zen` | Pfad zeigt auf `odoo_banking` statt `odoo_banking/addons` |
| Dienst startet nicht | Tippfehler im `addons_path`, Kommas ohne Leerzeichen sind ok |
| Alte Odoo-Version | Branch 19.0 auf Odoo 17/18 geht nicht |

Pfad testen:

```bash
ls /opt/odoo/extra/odoo_banking/addons/banking_community/__manifest__.py
ls /opt/odoo/extra/account-reconcile/account_reconcile_oca/__manifest__.py
ls /opt/odoo/extra/bank-statement-import/account_statement_import_online/__manifest__.py
```

Alle drei Dateien müssen existieren, dann Odoo neu starten und die
Apps-Liste aktualisieren.

---

## Danach

Weiter mit [SETUP.md](SETUP.md) ab Schritt 4 (Konten, Journals, Keys).
Die Module allein buchen noch nichts.
