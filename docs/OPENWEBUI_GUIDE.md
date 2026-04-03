# Open WebUI — Benutzerhandbuch

> **Für alle Nutzer — kein technisches Vorwissen nötig.**

---

## Inhaltsverzeichnis

- [Anmelden](#anmelden)
- [Die Oberfläche](#die-oberfläche)
- [Welches Modell wählen?](#welches-modell-wählen)
- [Commands — Spezialfunktionen](#commands--spezialfunktionen)
- [Dateien hochladen](#dateien-hochladen)
- [Audio transkribieren](#audio-transkribieren)
- [Bilder generieren](#bilder-generieren)
- [Tipps für bessere Antworten](#tipps-für-bessere-antworten)
- [Häufige Fragen](#häufige-fragen)

---

## Anmelden

Öffne den Browser und gehe zu:

```
http://SERVER-IP:8088
```

Beim ersten Besuch musst du ein Konto anlegen. Der Admin richtet deinen Zugang ein — du bekommst eine Einladung oder Login-Daten.

---

## Die Oberfläche

```
┌─────────────────────────────────────────┐
│  Neue Unterhaltung    Modell auswählen  │
├─────────────────────────────────────────┤
│                                         │
│         Chat-Verlauf                    │
│                                         │
├─────────────────────────────────────────┤
│  📎  [ Deine Nachricht eingeben... ] ↵  │
└─────────────────────────────────────────┘
```

- **Neue Unterhaltung** (links oben) — startet ein leeres Gespräch
- **Modell auswählen** (oben) — normalerweise auf Standard lassen
- **📎** — Dateien und Bilder anhängen

---

## Welches Modell wählen?

Das System wählt **automatisch** das passende Modell für dich. Du musst in der Regel nichts ändern.

| Was du machst | Automatisch gewählt | Kosten |
|--------------|---------------------|--------|
| Kurze Fragen, schnelle Aufgaben | DeepSeek V3.2 (schnell & günstig) | niedrig |
| Bild hochladen & analysieren | Qwen3-VL (Vision) | mittel |
| Lange Texte, komplexe Aufgaben | Qwen3-Coder Plus | mittel |
| Fehler / Ausfall | Gemini Flash (Fallback) | sehr niedrig |

> **Tipp:** Lass das Modell auf dem Standardwert. Eine manuelle Auswahl überschreibt das automatische Routing und kann unnötig Kosten verursachen.

---

## Commands — Spezialfunktionen

Tippe diese Befehle direkt in das Chat-Feld, um besondere Funktionen zu nutzen:

### Web-Recherche

```
/web Was sind die neuesten Entwicklungen bei KI?
```

```
/recherche Wetterbericht Berlin
```

Weitere Schreibweisen: `/search`, `/internet`, `/suche`, `/research`

Die KI sucht dann **live im Internet** (via SearXNG) statt aus ihrem trainierten Wissen zu antworten — ideal für aktuelle Informationen.

### Audio transkribieren

```
/transkribiere
```

Dann Audiodatei hochladen → Text kommt zurück.  
Alternativ: Audiodatei einfach hochladen → wird automatisch erkannt.

### Bilder generieren

```
Zeichne einen Hund auf einer Wiese
```

```
Generiere ein Bild von einem futuristischen Stadtbild
```

> Funktioniert nur wenn ein `TOGETHER_API_KEY` in der Konfiguration hinterlegt ist (Admin-Aufgabe).

---

## Dateien hochladen

Klicke auf **📎** neben dem Eingabefeld:

| Dateityp | Was passiert |
|----------|-------------|
| Bilder (JPG, PNG, ...) | KI analysiert das Bild, beantwortet Fragen dazu |
| PDF, Word, Text | KI liest den Inhalt und kann darüber sprechen |
| Audio (MP3, WAV, M4A, ...) | Automatische Transkription |

**Beispiel:** Masterarbeit als PDF hochladen und fragen:
```
Fasse die wichtigsten Punkte dieser Arbeit zusammen
```

---

## Audio transkribieren

Unterstützte Formate: MP3, WAV, M4A, AAC, OGG, FLAC, WebM

**Methode 1 — Automatisch:**  
Audiodatei einfach über 📎 hochladen → wird automatisch transkribiert.

**Methode 2 — Mit Command:**
```
/transkribiere
```
Dann 📎 hochladen.

Die Transkription erscheint als Text im Chat und wird für spätere Fragen gespeichert.

---

## Bilder generieren

Einfach auf Deutsch oder Englisch beschreiben, was du haben möchtest:

```
Erstelle ein Bild von einem Sonnenuntergang am Meer
```

```
Male eine bunte abstrakte Komposition
```

```
Zeichne ein realistisches Porträt einer alten Bibliothek
```

> **Hinweis:** Bildgenerierung benötigt einen Together.ai API-Key (vom Admin einzurichten). Wenn das Feature noch nicht aktiv ist, bekommst du eine entsprechende Meldung.

---

## Tipps für bessere Antworten

**Sei konkret**  
Statt: *"Hilf mir mit meinem Text"*  
Besser: *"Verbessere die Einleitung meines Anschreibens. Es soll formell klingen, aber nicht steif."*

**Füge Kontext hinzu**  
Erkläre kurz, wofür du die Antwort brauchst:
```
Ich schreibe eine Präsentation für Schüler der 8. Klasse. Erkläre mir das Thema Klimawandel einfach und verständlich.
```

**Lade Dateien hoch wenn passend**  
Statt einen langen Text einzutippen, einfach die Datei hochladen.

**Nutze Folge-Fragen**  
Die KI erinnert sich an den bisherigen Gesprächsverlauf:
```
Mach das nochmal, aber kürzer
```
```
Kannst du Punkt 3 ausführlicher erklären?
```

---

## Häufige Fragen

**Warum antwortet die KI manchmal langsam?**  
Die erste Anfrage nach dem Start dauert etwas länger (Modell-Warmup). Ab der zweiten Anfrage sind Antworten deutlich schneller.

**Werden meine Gespräche gespeichert?**  
Ja — das System speichert Konversationen und kann sie als Kontext in späteren Gesprächen nutzen. Das verbessert die Antwortqualität über die Zeit.

**Kann ich mehrere Dateien gleichzeitig hochladen?**  
Ja, du kannst mehrere Dateien in einer Nachricht anhängen.

**Was ist der Unterschied zu ChatGPT?**  
Der Stack nutzt mehrere verschiedene KI-Modelle und wählt automatisch das passende — so werden Kosten optimiert und du bekommst für jeden Anwendungsfall das beste Modell. Außerdem läuft die Infrastruktur selbst gehostet.

**Wie melde ich Probleme?**  
Wende dich an den Admin des Systems.

---

*Für technische Einrichtung und Entwickler-Tools: [VS Code Integration](VSCODE_SETUP.md)*  
*Zurück zur [Hauptdokumentation](../README.md)*
