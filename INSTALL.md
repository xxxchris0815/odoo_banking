# Installation auf Odoo 19 Community

Ziel: die Addons liegen auf dem `addons_path`, Odoo kennt sie, du
installierst **ein** Modul (`banking_community`). Danach Journals und
Keys wie in [SETUP.md](SETUP.md).

Voraussetzung: **Odoo 19 Community** (nicht 17/18, nicht Enterprise-only).
Python-Paket `requests` ist in Odoo schon dabei.

---

## Docker: was liegt wo

`/mnt/extra-addons` und `addons_path = /mnt/extra-addons,/mnt/extra-addons/commission`
sind Pfade **im Container**. Die Dateien liegen auf dem Host und sind
nur eingehängt. Die `odoo.conf` oft ebenfalls.

| Sache | Im Container | Anpassen auf |
| --- | --- | --- |
| Extra-Addons | `/mnt/extra-addons` | Host-Ordner, der dorthin gemountet ist |
| `commission` | `/mnt/extra-addons/commission` | schon vorhanden, nicht anfassen |
| Neue Repos | `/mnt/extra-addons/account-reconcile` usw. | dieselben Ordner auf dem Host anlegen |
| `addons_path` | `/etc/odoo/odoo.conf` oder Startkommando | die **Host-Datei** bzw. `docker-compose.yml`, nicht nur im laufenden Container |

### 1. Host-Ordner und Conf finden

Im Verzeichnis mit deiner `docker-compose.yml`:

```bash
docker compose ps
docker compose exec odoo cat /etc/odoo/odoo.conf
docker inspect "$(docker compose ps -q odoo)" --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'
```

Service heißt bei dir vielleicht nicht `odoo` — Namen aus `ps` nehmen.

Du brauchst zwei Zeilen aus den Mounts:

- Host-Pfad → `/mnt/extra-addons`  → hier klonen
- Host-Pfad → `/etc/odoo/odoo.conf` → hier `addons_path` ändern  
  (fehlt der Mount, steht der Pfad in der `command:` der Compose-Datei)

### 2. Auf dem Host klonen

Nicht `cd /mnt/extra-addons` auf dem Host, wenn es den Ordner dort nicht
gibt. Den **Source**-Pfad aus `docker inspect` nehmen, z. B.
`/data/odoo/addons` oder `./extra-addons`:

```bash
cd /DER/HOST/PFAD/DER/NACH/mnt/extra-addons/ZEIGT

git clone --branch 19.0 --depth 1 https://github.com/OCA/account-reconcile
git clone --branch 19.0 --depth 1 https://github.com/OCA/bank-statement-import
git clone --branch cursor/community-banking-stack-f606 https://github.com/xxxchris0815/odoo_banking
```

Im Container muss danach gelten:

```bash
docker compose exec odoo ls /mnt/extra-addons/odoo_banking/addons/banking_community/__manifest__.py
docker compose exec odoo ls /mnt/extra-addons/account-reconcile/account_reconcile_oca/__manifest__.py
```

### 3. `addons_path` auf dem Host erweitern

In der gemounteten Conf (oder in `command:` / `--addons-path=`):

```ini
addons_path = /mnt/extra-addons,/mnt/extra-addons/commission,/mnt/extra-addons/account-reconcile,/mnt/extra-addons/bank-statement-import,/mnt/extra-addons/odoo_banking/addons
```

Die Pfade bleiben Container-Pfade. Nur die Datei, die du editierst, liegt
auf dem Host. Nur im Container `vi /etc/odoo/odoo.conf` ändern geht verloren,
wenn die Conf nicht gemountet ist.

### 4. Container neu starten, dann Apps-Liste

```bash
docker compose restart odoo
```

Danach in Odoo: Entwicklermodus → Apps-Liste aktualisieren →
**Community Banking Stack**. Nur die Liste neu laden ohne Restart reicht
nicht, der Prozess hat den alten `addons_path` noch im Speicher.

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

## Apps-Liste aktualisiert, `banking_community` fehlt

Die Liste neu laden reicht nicht, wenn Odoo den Ordner nicht als Modul
sieht. `/mnt/extra-addons/odoo_banking` ist **kein** Modul — das Manifest
liegt in `odoo_banking/addons/banking_community/`.

**Im Container** (nicht nur auf dem Host) prüfen:

```bash
ls /mnt/extra-addons/odoo_banking/addons/banking_community/__manifest__.py
ls /mnt/extra-addons/account-reconcile/account_reconcile_oca/__manifest__.py
ls /mnt/extra-addons/bank-statement-import/account_statement_import_online/__manifest__.py
```

Fehlt die erste Datei: Klon liegt nicht im gemounteten Volume, oder du
stehst auf dem Host-Pfad statt im Container.

Sind die Dateien da, muss `addons_path` **genau so** aussehen (und Odoo
danach neu gestartet werden, nicht nur Apps-Liste):

```ini
addons_path = /mnt/extra-addons,/mnt/extra-addons/commission,/mnt/extra-addons/account-reconcile,/mnt/extra-addons/bank-statement-import,/mnt/extra-addons/odoo_banking/addons
```

Ob der laufende Prozess das schon hat:

```bash
ps aux | grep -E 'odoo|odoo-bin'
# oder in der Odoo-Shell / Log beim Start: addons paths
```

Dann Container/Dienst **neu starten**. Erst danach Entwicklermodus →
Apps-Liste aktualisieren. Suche nach **Community Banking Stack**, Filter
*Apps* aus. Technischer Name: `banking_community`.

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

## Update (neue Version dieses Repos)

Auf dem Host, nicht im Container:

```bash
cd /opt/odoo/extra-addons/odoo_banking
git fetch origin
git checkout cursor/community-banking-stack-f606
git pull origin cursor/community-banking-stack-f606
```

Dann die Module **upgraden** (nicht neu installieren). Datenbanknamen
ersetzen, falls er nicht `odoo` ist (`grep db_name` in der Conf):

```bash
cd /opt/odoo
docker compose exec odoo odoo -c /etc/odoo/odoo.conf \
  -d odoo \
  -u banking_community,account_statement_import_online_gocardless_payments \
  --stop-after-init --http-port=8070
```

Danach den normalen Container weiterlaufen lassen bzw.

```bash
docker compose restart
```

Ohne Konsole: Apps → Filter *Apps* aus → Community Banking Stack →
**Aktualisieren**.

---

## Danach

Weiter mit [SETUP.md](SETUP.md) ab Schritt 4 (Konten, Journals, Keys).
Die Module allein buchen noch nichts.
