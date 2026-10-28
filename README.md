# Denon DN-500BD MKII – Home Assistant

Home-Assistant-Integration für den Blu-ray-Player **Denon Professional DN-500BD MKII** über IP-Steuerung (TCP-Port 9030). Grundlage ist der *Serial Command Protocol Guide* v1.00.

> **Status 0.1.0:** gegen die Protokoll-Doku und einen simulierten Player entwickelt, **noch nicht an echter Hardware getestet**. Vor dem produktiven Einsatz einmal das Probe-Skript laufen lassen (siehe unten).

## Funktionen

**Media Player**
- Play, Pause, Stop
- Kapitel vor/zurück (Next/Prev Track)
- Ein/Aus
- Position und Dauer als Timecode-Rückmeldung (Dauer = Laufzeit + Restzeit)
- `media_track` = aktuelles Kapitel, Titel als „Titel x/y · Kapitel a/b“

**Sensoren**
| Sensor | Inhalt |
|---|---|
| Timecode | Laufzeit `H:MM:SS` |
| Restzeit | `H:MM:SS` |
| Gesamtdauer | Laufzeit + Restzeit, `H:MM:SS` |
| Kapitel / Kapitel im Titel | aktuelles Kapitel, Anzahl Kapitel im aktuellen Titel |
| Titel / Titel auf Disc | aktueller Titel, Anzahl Titel |
| Transport | `play`, `pause`, `scan_forward`, `scan_reverse`, `slow_forward`, `step`, `menu`, `home` … |
| Disc-Status | `disc_in`, `no_disc`, `tray_open`, `tray_closed` … |
| Disc-Typ, Audioformat, Audiokanäle | Diagnose |
| Laufzeit Sekunden | Zahl, standardmäßig deaktiviert |

**Buttons:** Suchlauf vorwärts, Suchlauf rückwärts, nächster/vorheriger Titel, Lade öffnen/schließen (Lade standardmäßig deaktiviert), Menübedienung: Cursor hoch/runter/links/rechts, Enter, Zurück, Top-Menü, Popup-Menü, Home.

**Aktionen** (Ziel: der Media Player)
| Aktion | Felder |
|---|---|
| `dn500bd.goto_chapter` | `chapter` 1–2000 |
| `dn500bd.goto_title` | `title` 1–2000 |
| `dn500bd.scan` | `direction` forward/reverse, `speed` fast/slow – mit Play zurück zur Normalwiedergabe |
| `dn500bd.tray` | `action` open/close |
| `dn500bd.send_key` | `key`: `up`, `down`, `left`, `right`, `enter`, `return`, `top_menu`, `popup_menu`, `home`, `setup`, `option` |
| `dn500bd.send_raw` | `command` (z. B. `@0?ST`), `window` – liefert ACK/NACK und alle empfangenen Pakete als Antwort zurück |

Ein Locate auf Timecode gibt es nicht – das Protokoll bietet dafür keinen Befehl.

## Installation

1. HACS → Benutzerdefinierte Repositories → `https://github.com/Haraldbbag/DN500BD` (Typ Integration) → installieren.
2. Home Assistant neu starten.
3. Einstellungen → Geräte & Dienste → Integration hinzufügen → **Denon DN-500BD MKII**.
4. Name, IP und Port (9030) eintragen. Den Player dabei **eingeschaltet** lassen: Dann wird die MAC-Adresse gelesen und als eindeutige ID verwendet (sonst die IP).

Für jeden Player einen eigenen Eintrag anlegen.

## Optionen

| Option | Standard | Bedeutung |
|---|---|---|
| Abfrageintervall bei Wiedergabe | 1 s | HA rechnet die Position dazwischen selbst hoch |
| Abfrageintervall sonst | 5 s | |
| ACK-Timeout | 300 ms | Doku nennt 30 ms; mehr Luft gegen Fehl-Timeouts |
| Antwort-Timeout | 500 ms | |
| Richtungscode Rückwärts-Scan | `R` | nicht dokumentiert, siehe unten |
| Statusmeldungen quittieren | an | unaufgeforderte Meldungen werden mit ACK bestätigt |

**Recorder:** Timecode und Restzeit ändern sich jede Sekunde. Wer das nicht in der Datenbank haben will:

```yaml
recorder:
  exclude:
    entity_globs:
      - sensor.*_timecode
      - sensor.*_restzeit
```

## Protokoll-Verhalten

- Immer nur ein Befehl gleichzeitig, mindestens 40 ms Abstand.
- Statusabfragen werden bei fehlendem ACK bis zu zweimal wiederholt.
- **Steuerbefehle werden nie automatisch wiederholt**, damit z. B. „nächstes Kapitel“ nicht doppelt ausgeführt wird.
- Ausgeschaltet beantwortet der Player `@0?PW` nicht → Zustand „aus“.
- `@0BDERBUSY` wird als Fehler gemeldet.

## Offene Punkte (vor Ort klären)

Die Doku lässt einiges offen. Das Probe-Skript prüft genau diese Punkte:

1. **Rückwärts-Suchlauf:** dokumentiert ist nur `F`. Vermutet wird `@0PCSLSRf`.
2. **Bezug von ET/RM:** Zeit im Titel oder im Kapitel? Einfluss des Time Mode (`PCTMD`)? Am Gerät geprüft: `?ET`/`?RM` kommen als nackte Ziffern direkt nach `ack` zurück (z. B. `ack0000052`), ohne `@0ET` – wird seit 0.1.4 so ausgewertet. Ohne laufenden Titel (Menü) kommt nur `ack`.
3. **Stop-Status:** Für Stop ist keine `@0?ST`-Antwort dokumentiert.
4. **Unaufgeforderte Statusmeldungen:** Laut Tabelle gibt es keine, laut Kapitel 3-4-3 schon.
5. **Antwortformat** von `?PCTG`/`?PCGp` (Doku zeigt die Antwort mit `?`; beide Varianten werden akzeptiert).
6. **Mehrere Suchlauf-Geschwindigkeiten**: Bringt wiederholtes Senden höhere Stufen?

## Probe-Skript (ohne Home Assistant)

Benötigt nur Python 3.11+. Aus dem Repo-Ordner starten:

```bash
# nur lesen: alle Statusabfragen + 10 s auf Meldungen hören
python3 tools/dn500bd_probe.py 192.168.x.y

# zusätzlich Transport testen – ÄNDERT DIE WIEDERGABE
# (Disc mit mehreren Kapiteln läuft, keine Vorstellung)
python3 tools/dn500bd_probe.py 192.168.x.y --actions

# einzelne Befehle
python3 tools/dn500bd_probe.py 192.168.x.y --raw "@0?ST" --raw "@0PCSLSRf"
```

Das Skript schreibt eine Logdatei `dn500bd_<ip>_<zeit>.log`. Diese dann zurückgeben, damit die Integration angepasst werden kann.

## Entwicklung

```bash
python3 tests/test_client.py
```

Die Tests laufen gegen einen simulierten Player (`tests/fake_device.py`) und brauchen kein Home Assistant. `custom_components/dn500bd/client.py` ist bewusst frei von Home-Assistant-Abhängigkeiten, damit Integration, Probe-Skript und Tests denselben Protokollcode nutzen.

Bei Änderungen: Version in `manifest.json` erhöhen, Tests laufen lassen, nach `main` pushen.
