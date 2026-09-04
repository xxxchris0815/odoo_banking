# Installation auf Odoo 19 Community

Ziel: die Addons liegen auf dem `addons_path`, Odoo kennt sie, du
installierst **ein** Modul (`banking_community`). Danach Journals und
Keys wie in [SETUP.md](SETUP.md).

Voraussetzung: **Odoo 19 Community** (nicht 17/18, nicht Enterprise-only).
Python-Paket `requests` ist in Odoo schon dabei.

---

## 1. Wer Odoo wirklich startet

Es braucht **keinen** Linux-User namens `odoo`. Das war nur ein
Beispielname. Nimm den Account, mit dem der Prozess schon läuft — oft
dein eigener, `www-data`, `ubuntu`, oder ein Docker-User.

```bash
ps aux | grep -E 'odoo|odoo-bin'
```

Die erste Spalte ist der Systemuser. Die Kommandozeile zeigt oft schon
`-c /pfad/zu/odoo.conf` oder `--addons-path=…`.

Konfig suchen, falls sie nicht in der Prozesszeile steht:

```bash
ls /etc/odoo/odoo.conf /etc/odoo.conf ./odoo.conf ~/odoo.conf 2>/dev/null
```

In der Datei `addons_path` notieren. Neue Repos müssen als Ordner in
diesem Pfad landen. Bei uns zählt `…/odoo_banking/addons`, nicht das
Repo-Root.

---

## 2. Repos klonen

Als **dein** User (oder root), in einen Ordner, den Odoo lesen darf.
Nicht nach `odoo/addons` legen (das überschreibt Updates).

```bash
mkdir -p "$HOME/odoo-extra"
cd "$HOME/odoo-extra"

git clone --branch 19.0 --depth 1 \
  https://github.com/OCA/account-reconcile

git clone --branch 19.0 --depth 1 \
  https://github.com/OCA/bank-statement-import

git clone --branch cursor/community-banking-stack-f606 \
  https://github.com/xxxchris0815/odoo_banking
```

`$HOME/odoo-extra` kannst du durch jeden bestehenden Extra-Addons-Ordner
ersetzen, den du in `addons_path` schon nutzt.

`account-reconcile` und `bank-statement-import` **sind selbst**
Addons-Pfade. Bei `odoo_banking` ist der Addons-Pfad `addons/`.

---

## 3. `addons_path` erweitern

### Klassische Installation (`odoo.conf`)

Bestehenden `addons_path` **ergänzen**, nicht ersetzen:

Den **bestehenden** Core-Pfad stehen lassen und nur die drei neuen
Ordner anhängen. Beispiel, wenn du nach `$HOME/odoo-extra` geklont hast:

```ini
addons_path = /usr/lib/python3/dist-packages/odoo/addons,/home/DEINUSER/odoo-extra/account-reconcile,/home/DEINUSER/odoo-extra/bank-statement-import,/home/DEINUSER/odoo-extra/odoo_banking/addons
```

`DEINUSER` durch deinen Login ersetzen. Wenn Odoo schon einen Extra-Pfad
hat, die drei Klone dorthin legen und nur fehlende Einträge ergänzen.

Odoo neu starten — je nachdem, wie du es betreibst:

```bash
# systemd (Name mit tab oder systemctl list-units | grep -i odoo finden)
sudo systemctl restart odoo
sudo systemctl restart odoo19

# Docker
docker compose restart

# manuell gestartet: Prozess beenden und dasselbe Kommando wieder ausführen
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
# dasselbe Binary und dieselbe conf, mit der Odoo schon läuft
odoo -c /pfad/zu/odoo.conf -d DEINE_DATENBANK \
  -i banking_community --stop-after-init
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
ls "$HOME/odoo-extra/odoo_banking/addons/banking_community/__manifest__.py"
ls "$HOME/odoo-extra/account-reconcile/account_reconcile_oca/__manifest__.py"
ls "$HOME/odoo-extra/bank-statement-import/account_statement_import_online/__manifest__.py"
```

Alle drei Dateien müssen existieren, dann Odoo neu starten und die
Apps-Liste aktualisieren.

---

## Danach

Weiter mit [SETUP.md](SETUP.md) ab Schritt 4 (Konten, Journals, Keys).
Die Module allein buchen noch nichts.
