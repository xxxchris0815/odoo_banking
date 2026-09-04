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

In den Ordner, der **schon** in `addons_path` steht — nicht nach
`$HOME` und nicht nach `odoo/addons`.

Sehr oft (Docker): `/mnt/extra-addons`. Hast du z. B.

```ini
addons_path = /mnt/extra-addons,/mnt/extra-addons/commission,
```

dann auf dem Host (oder im Container, wenn dort git geht):

```bash
cd /mnt/extra-addons

git clone --branch 19.0 --depth 1 \
  https://github.com/OCA/account-reconcile

git clone --branch 19.0 --depth 1 \
  https://github.com/OCA/bank-statement-import

git clone --branch cursor/community-banking-stack-f606 \
  https://github.com/xxxchris0815/odoo_banking
```

`/mnt/extra-addons` allein reicht nicht für die neuen Repos: OCA hat die
Module eine Ebene tiefer, dieses Repo unter `odoo_banking/addons`. Deshalb
`addons_path` so ergänzen:

```ini
addons_path = /mnt/extra-addons,/mnt/extra-addons/commission,/mnt/extra-addons/account-reconcile,/mnt/extra-addons/bank-statement-import,/mnt/extra-addons/odoo_banking/addons
```

`commission` kann stehen bleiben. Odoo neu starten, Apps-Liste aktualisieren,
**Community Banking Stack** installieren.

---

## 3. `addons_path` erweitern

### Klassische Installation (`odoo.conf`)

Bestehenden `addons_path` **ergänzen**, nicht ersetzen:

Den **bestehenden** Pfad stehen lassen und nur die drei Repo-Wurzeln
anhängen. Wenn Extra-Addons schon unter `/mnt/extra-addons` liegen:

```ini
addons_path = /mnt/extra-addons,/mnt/extra-addons/commission,/mnt/extra-addons/account-reconcile,/mnt/extra-addons/bank-statement-import,/mnt/extra-addons/odoo_banking/addons
```

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

Wenn `/mnt/extra-addons` schon ein Volume ist (wie bei dir): auf dem
**Host-Ordner** klonen, der dort gemountet ist. Keine neuen Volumes.
Nur `addons_path` um die drei Unterordner erweitern, Container neu starten.

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
ls /mnt/extra-addons/odoo_banking/addons/banking_community/__manifest__.py
ls /mnt/extra-addons/account-reconcile/account_reconcile_oca/__manifest__.py
ls /mnt/extra-addons/bank-statement-import/account_statement_import_online/__manifest__.py
```

Alle drei Dateien müssen existieren, dann Odoo neu starten und die
Apps-Liste aktualisieren.

---

## Danach

Weiter mit [SETUP.md](SETUP.md) ab Schritt 4 (Konten, Journals, Keys).
Die Module allein buchen noch nichts.
