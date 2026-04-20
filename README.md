# media-dedup

Deduplica una o due cartelle di foto e video, senza mai cancellare nulla per sbaglio.

Funziona sia su **foto** (`.jpg`, `.jpeg`, `.png`) che su **video** (`.mp4`, `.mov`, `.avi`, `.mkv` e altri). Rileva i doppioni esatti (SHA-256) e quelli percettivi (pHash), usa una cache su disco per essere veloce anche su collezioni da decine di migliaia di file.

---

## Modalità disponibili

### `--single` — pulizia in-place di una cartella

Scansiona una sola cartella e **cancella** i file identici (SHA-256 uguale = pixel identici), tenendo sempre la copia con risoluzione maggiore. I file *simili ma non identici* (es. crop, resize, ricodifica) vengono copiati in `/considering` per la revisione manuale senza toccare gli originali.

```
python main.py --single "C:\media\archivio" --out "C:\media\report"
```

### `--a` / `--b` — unione di due cartelle

Unisce due cartelle eliminando i doppioni, **senza mai toccare gli originali**. I file vengono copiati nella cartella di output.

```
python main.py --a "C:\media\backup1" --b "C:\media\backup2" --out "C:\media\risultato"
```

### `--dry-run` — anteprima senza modifiche

Disponibile in entrambe le modalità. Simula l'intera scansione e scrive il report CSV (con suffisso `_dryrun`) senza spostare, copiare o cancellare nulla.

```
python main.py --single "C:\media\archivio" --out "C:\media\report" --dry-run
```

---

## Struttura dell'output

```
output/
├── merged/                        ← (solo modalità due cartelle)
│   ├── foto_vacanze.jpg           ← file unici + la copia migliore di ogni duplicato
│   └── video_compleanno.mp4
├── considering/                   ← file simili ma non identici, da verificare a mano
│   ├── group_0001__foto_a.jpg     ← stesso prefisso = stesso gruppo
│   ├── group_0001__foto_b.jpg
│   └── group_0002__video_x.mp4
└── report_YYYYMMDD_HHMMSS.csv     ← log completo di ogni decisione
```

In `/considering` i file sono in una **cartella piatta**: ordinando per nome, i file dello stesso gruppo si affiancano automaticamente.

---

## Installazione

```bash
git clone https://github.com/<tuo-username>/media-dedup.git
cd media-dedup
pip install -r requirements.txt
```

**Dipendenze:**
- `Pillow` — lettura immagini
- `ImageHash` — hash percettivo (pHash)
- `opencv-python` — estrazione frame dai video

> Su alcune macchine può servire aggiungere `--break-system-packages` al comando pip se si usa Python di sistema invece di un virtualenv.

---

## Opzioni

| Opzione | Default | Descrizione |
|---|---|---|
| `--single CARTELLA` | — | Modalità in-place (obbligatorio se non si usa --a/--b) |
| `--a CARTELLA_A` | — | Prima cartella sorgente |
| `--b CARTELLA_B` | — | Seconda cartella sorgente |
| `--out OUTPUT` | — | Cartella di output (obbligatoria) |
| `--threshold N` | `10` | Soglia pHash per /considering |
| `--dry-run` | off | Simula senza modificare nulla |

### Guida alla soglia pHash

| Valore | Comportamento |
|---|---|
| ≤ 5 | Conservativo: solo quasi-identici in /considering |
| **10** | **Default consigliato: crop lievi, resize moderate** |
| ≥ 15 | Aggressivo: include modifiche più marcate |

---

## Come funziona

### Modalità `--single`

```
Tutti i file (foto + video)
        │
        ▼
   SHA-256 hash  ←──── cache .dedup_cache.json
        │
        ├─ Identici → tieni risoluzione maggiore → CANCELLA la copia peggiore
        │
        └─ Unici → restano in-place (non vengono toccati)
```

### Modalità due cartelle

```
File da cartella A + cartella B
        │
        ▼
   SHA-256 hash  ←──── cache .dedup_cache.json
        │
        ├─ Identici → tieni risoluzione maggiore → /merged
        │
        ▼
   pHash + BK-Tree  (foto: pHash diretto | video: pHash frame centrale + pre-filtro durata)
        │
        ├─ Distanza ≤ soglia → simili → /considering/group_XXXX__*
        │
        └─ Distanza > soglia → unici → /merged
```

**Cache:** gli hash SHA-256 e pHash vengono salvati in `.dedup_cache.json` nella cartella sorgente. Se un file non è cambiato (stessa dimensione e data di modifica), l'hash viene riletto dalla cache invece di essere ricalcolato. Questo rende i run successivi molto più veloci, specialmente con i video.

---

## Recupero file con errori

Se durante una scansione alcuni file non sono stati leggibili, il report CSV li registra con `tipo=errore`. Per copiarli in una cartella di sicurezza:

```
python recover_errors.py --report "C:\media\report\report_20240101.csv" --out "C:\media\recuperati"
```

Opzionalmente si possono recuperare anche altri tipi di file dal report:

```
python recover_errors.py --report "..." --out "..." --also simile duplicato_esatto
```

---

## Workflow consigliato (prima volta su una grande collezione)

1. Esegui con `--dry-run` per vedere cosa verrebbe fatto
2. Controlla il report CSV `_dryrun`
3. Se tutto è corretto, riesegui senza `--dry-run`
4. In modalità `--single`: lo script chiede di digitare `CANCELLA` prima di procedere
5. Controlla `/considering`: ogni gruppo ha il prefisso `group_XXXX__`
6. Solo quando sei sicuro: cancella manualmente i file originali (modalità due cartelle)

---

## Estensioni video supportate

`.mp4` `.mov` `.avi` `.mkv` `.m4v` `.wmv` `.flv` `.webm` `.3gp` `.mts` `.m2ts`

## Estensioni immagini supportate

`.jpg` `.jpeg` `.png`
