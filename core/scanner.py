"""
scanner.py — Logica principale di scansione e deduplicazione.

Modalità --single (single_mode=True):
  1. Raccolta ricorsiva di tutti i file supportati (foto + video)
  2. SHA-256 → duplicati esatti → cancella la copia peggiore (in-place)
  3. Nessuna fase pHash (obiettivo: rimuovere solo i doppioni certi)

Modalità due cartelle (single_mode=False):
  1. Raccolta ricorsiva da entrambe le cartelle
  2. SHA-256 → duplicati esatti → tieni risoluzione maggiore → /merged
  3. pHash + BK-Tree → simili → /considering (foto e video)
  4. Copia file + scrittura report CSV

In entrambe le modalità:
  - dry_run=True → nessuna operazione su disco, solo calcolo e report simulato
  - HashCache → evita di ricalcolare hash su file già visti
  - Nulla viene mai cancellato dagli originali in modalità due cartelle
"""

import csv
import hashlib
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable

import imagehash
from PIL import Image

from core.bktree import BKTree
from core.cache import HashCache
from core.models import (
    DuplicatePair,
    ImageRecord,
    ScanConfig,
    ScanResult,
    SimilarGroup,
)
from core.video import video_info, video_phash


# ---------------------------------------------------------------------------
# Utilità di hashing (con cache)
# ---------------------------------------------------------------------------

def _sha256(path: Path, cache: HashCache) -> str:
    cached = cache.get_sha256(path)
    if cached:
        return cached
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    result = h.hexdigest()
    cache.set_sha256(path, result)
    return result


def _image_phash(path: Path, cache: HashCache) -> object:
    cached = cache.get_phash(path)
    if cached:
        return imagehash.hex_to_hash(cached)
    with Image.open(path) as img:
        ph = imagehash.phash(img)
    cache.set_phash(path, str(ph))
    return ph


def _video_phash(path: Path, cache: HashCache) -> object:
    cached = cache.get_phash(path)
    if cached:
        return imagehash.hex_to_hash(cached)
    ph = video_phash(path)
    cache.set_phash(path, str(ph))
    return ph


def _build_record(path: Path, sha: str, config: ScanConfig, cache: HashCache) -> ImageRecord:
    """
    Costruisce un ImageRecord per un file, gestendo sia immagini che video.
    phash è None in modalità single (non viene calcolato).
    """
    is_video = path.suffix.lower() in config.video_extensions

    if is_video:
        try:
            width, height, duration = video_info(path)
        except Exception:
            width, height, duration = 0, 0, None
        return ImageRecord(
            path=path, sha256=sha, phash=None,
            width=width, height=height, size_bytes=path.stat().st_size,
            is_video=True, duration=duration,
        )
    else:
        with Image.open(path) as img:
            width, height = img.size
        return ImageRecord(
            path=path, sha256=sha, phash=None,
            width=width, height=height, size_bytes=path.stat().st_size,
            is_video=False, duration=None,
        )


# ---------------------------------------------------------------------------
# Raccolta file
# ---------------------------------------------------------------------------

def collect_files(folder: Path, extensions: frozenset) -> list[Path]:
    """Scansione ricorsiva della cartella, restituisce tutti i file supportati."""
    return [
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in extensions
    ]


# ---------------------------------------------------------------------------
# Fase 1 — Deduplicazione esatta (SHA-256)
# ---------------------------------------------------------------------------

def deduplicate_exact(
    paths: list[Path],
    config: ScanConfig,
    cache: HashCache,
    progress: Callable[[int, int, str], None] | None = None,
) -> tuple[list[ImageRecord], list[DuplicatePair], list[tuple[Path, str]]]:
    """
    Per ogni gruppo di file con SHA-256 identico, tiene quello a risoluzione
    maggiore (o dimensione file maggiore in caso di parità).

    Restituisce:
      - records:    un ImageRecord per ogni hash unico (il "migliore")
      - duplicates: coppie (tenuto, scartato)
      - errors:     file non leggibili
    """
    sha_map: dict[str, ImageRecord] = {}
    duplicates: list[DuplicatePair] = []
    errors: list[tuple[Path, str]] = []

    total = len(paths)
    for i, path in enumerate(paths):
        if progress:
            progress(i + 1, total, f"SHA-256: {path.name}")
        try:
            sha = _sha256(path, cache)
            record = _build_record(path, sha, config, cache)

            if sha in sha_map:
                existing = sha_map[sha]
                if (record.resolution > existing.resolution or
                        (record.resolution == existing.resolution
                         and record.size_bytes > existing.size_bytes)):
                    duplicates.append(
                        DuplicatePair(kept=record.path, skipped=existing.path, method="sha256")
                    )
                    sha_map[sha] = record
                else:
                    duplicates.append(
                        DuplicatePair(kept=existing.path, skipped=record.path, method="sha256")
                    )
            else:
                sha_map[sha] = record

        except Exception as e:
            errors.append((path, str(e)))

    return list(sha_map.values()), duplicates, errors


# ---------------------------------------------------------------------------
# Fase 2 — Deduplicazione percettiva (pHash + BK-Tree) — solo modalità due cartelle
# ---------------------------------------------------------------------------

def _duration_mismatch(r1: ImageRecord, r2: ImageRecord, tolerance: float) -> bool:
    """True se entrambi sono video con durate troppo diverse per essere simili."""
    if not (r1.is_video and r2.is_video):
        return False
    if r1.duration is None or r2.duration is None:
        return False   # conservativo: non filtrare se manca la durata
    longer = max(r1.duration, r2.duration)
    if longer == 0:
        return False
    return abs(r1.duration - r2.duration) / longer > tolerance


def deduplicate_perceptual(
    records: list[ImageRecord],
    config: ScanConfig,
    cache: HashCache,
    progress: Callable[[int, int, str], None] | None = None,
) -> tuple[list[Path], list[SimilarGroup], list[tuple[Path, str]]]:
    """
    Usa pHash e BK-Tree per trovare file simili (ritagliati, ridimensionati,
    ricodificati). Supporta sia immagini che video.

    Per i video: pHash del frame centrale per il BK-Tree (veloce),
    con pre-filtro sulla durata per ridurre i falsi positivi.

    Restituisce:
      - unique_paths:    file senza simili
      - similar_groups:  gruppi di simili
      - errors:          file non leggibili
    """
    tree = BKTree()
    errors: list[tuple[Path, str]] = []

    # Calcola pHash e popola il BK-Tree
    total = len(records)
    for i, record in enumerate(records):
        if progress:
            progress(i + 1, total, f"{'Video' if record.is_video else 'pHash'}: {record.path.name}")
        try:
            if record.is_video:
                ph = _video_phash(record.path, cache)
            else:
                ph = _image_phash(record.path, cache)
            record.phash = ph
            tree.insert(ph, record.path)
        except Exception as e:
            errors.append((record.path, str(e)))

    # Trova gruppi simili
    processed: set[Path] = set()
    similar_groups: list[SimilarGroup] = []
    unique_paths: list[Path] = []

    path_to_record: dict[Path, ImageRecord] = {r.path: r for r in records}

    for record in records:
        if record.path in processed or record.phash is None:
            continue

        matches = tree.search(record.phash, config.phash_threshold)

        neighbors = []
        for dist, path in matches:
            if dist == 0 or path in processed or path == record.path:
                continue
            neighbor = path_to_record.get(path)
            if neighbor is None:
                continue
            # Pre-filtro durata video
            if _duration_mismatch(record, neighbor, config.duration_tolerance):
                continue
            neighbors.append((dist, path))

        if not neighbors:
            unique_paths.append(record.path)
            processed.add(record.path)
        else:
            group_paths = [record.path] + [p for _, p in neighbors]
            group_records = [path_to_record[p] for p in group_paths if p in path_to_record]
            best_record = max(group_records, key=lambda r: (r.resolution, r.size_bytes))
            others = [r.path for r in group_records if r.path != best_record.path]
            max_dist = max(d for d, _ in neighbors)

            similar_groups.append(
                SimilarGroup(best=best_record.path, others=others, phash_distance=max_dist)
            )
            for p in group_paths:
                processed.add(p)

    return unique_paths, similar_groups, errors


# ---------------------------------------------------------------------------
# Fase 3a — Cancellazione duplicati esatti (solo modalità --single)
# ---------------------------------------------------------------------------

def delete_exact_duplicates(
    pairs: list[DuplicatePair],
    dry_run: bool = False,
    progress: Callable[[int, int, str], None] | None = None,
) -> list[tuple[Path, str]]:
    """
    Cancella fisicamente i file "skipped" (duplicati esatti SHA-256).
    Se dry_run=True, stampa cosa verrebbe cancellato senza toccare nulla.
    """
    errors: list[tuple[Path, str]] = []
    total = len(pairs)
    for i, pair in enumerate(pairs):
        if progress:
            progress(i + 1, total, f"{'[DRY] ' if dry_run else ''}Cancello: {pair.skipped.name}")
        if dry_run:
            continue
        try:
            pair.skipped.unlink()
        except Exception as e:
            errors.append((pair.skipped, str(e)))
    return errors


# ---------------------------------------------------------------------------
# Fase 3b — Copia file in output (solo modalità due cartelle)
# ---------------------------------------------------------------------------

def _safe_copy(src: Path, dest_dir: Path) -> Path:
    """Copia src in dest_dir gestendo conflitti di nome."""
    dest = dest_dir / src.name
    if dest.exists():
        dest = dest_dir / f"{src.stem}__{src.stat().st_size}{src.suffix}"
    shutil.copy2(src, dest)
    return dest


def _safe_copy_as(src: Path, dest_dir: Path, new_name: str) -> Path:
    """Copia src in dest_dir usando new_name; gestisce conflitti."""
    dest = dest_dir / new_name
    if dest.exists():
        stem = Path(new_name).stem
        suffix = Path(new_name).suffix
        dest = dest_dir / f"{stem}__{src.stat().st_size}{suffix}"
    shutil.copy2(src, dest)
    return dest


def copy_results(result: ScanResult, config: ScanConfig) -> None:
    """
    Copia i risultati in output.
    In dry_run: stampa cosa verrebbe copiato senza farlo.
    """
    considering_dir = config.output_dir / "considering"

    if not config.dry_run:
        considering_dir.mkdir(parents=True, exist_ok=True)

    # /merged — solo modalità due cartelle
    if not config.single_mode:
        merged_dir = config.output_dir / "merged"
        if not config.dry_run:
            merged_dir.mkdir(parents=True, exist_ok=True)

        for path in result.unique:
            if config.dry_run:
                print(f"  [DRY] → merged/{path.name}")
            else:
                _safe_copy(path, merged_dir)

        for group in result.similar_groups:
            if config.dry_run:
                print(f"  [DRY] → merged/{group.best.name}")
            else:
                _safe_copy(group.best, merged_dir)

    # /considering — entrambe le modalità
    for idx, group in enumerate(result.similar_groups, start=1):
        prefix = f"group_{idx:04d}__"
        if config.dry_run:
            print(f"  [DRY] → considering/{prefix}{group.best.name}")
            for other in group.others:
                print(f"  [DRY] → considering/{prefix}{other.name}")
        else:
            _safe_copy_as(group.best, considering_dir, f"{prefix}{group.best.name}")
            for other in group.others:
                _safe_copy_as(other, considering_dir, f"{prefix}{other.name}")


# ---------------------------------------------------------------------------
# Fase 4 — Report CSV
# ---------------------------------------------------------------------------

def write_report(result: ScanResult, config: ScanConfig) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = "_dryrun" if config.dry_run else ""
    report_path = config.output_dir / f"report_{timestamp}{suffix}.csv"

    config.output_dir.mkdir(parents=True, exist_ok=True)

    with open(report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["tipo", "decisione", "file_principale", "file_alternativo", "dettaglio"])

        for path in result.unique:
            writer.writerow(["unico", "merged", str(path), "", ""])

        for pair in result.exact_duplicates:
            if config.single_mode:
                decisione = "[DRY] tenuto / sarebbe cancellato" if config.dry_run else "tenuto in-place / CANCELLATO"
            else:
                decisione = "merged (migliore) / ignorato (copia)"
            writer.writerow([
                "duplicato_esatto", decisione,
                str(pair.kept), str(pair.skipped),
                "SHA-256 identico — tenuta risoluzione maggiore",
            ])

        for idx, group in enumerate(result.similar_groups, start=1):
            for other in group.others:
                writer.writerow([
                    "simile", f"merged (migliore) / considering (prefisso group_{idx:04d}__)",
                    str(group.best), str(other),
                    f"pHash distance={group.phash_distance:.1f}",
                ])

        for path, error in result.errors:
            writer.writerow(["errore", "ignorato", str(path), "", error])

    return report_path


# ---------------------------------------------------------------------------
# Entry point principale (usato anche dalla futura GUI)
# ---------------------------------------------------------------------------

def run_scan(
    config: ScanConfig,
    progress: Callable[[str, int, int], None] | None = None,
    confirm_delete: Callable[[list], bool] | None = None,
) -> ScanResult:
    """
    Esegue la scansione completa.

    Parametri:
      config          — configurazione della scansione
      progress        — callback opzionale: progress(fase, corrente, totale)
      confirm_delete  — callback in modalità --single: riceve la lista di
                        DuplicatePair, restituisce True per procedere.
                        Se None, la cancellazione avviene senza ulteriore conferma.
    """
    def _prog(current, total, label):
        if progress:
            progress(label, current, total)
        else:
            pct = int(current / total * 100) if total else 0
            print(f"  [{pct:3d}%] {label}", end="\r")

    # Carica la cache
    cache = HashCache(config.cache_file)
    if config.cache_file:
        print(f"\n💾 Cache: {len(cache)} voci caricate da {config.cache_file.name}")

    if config.dry_run:
        print("\n  ⚠️  DRY RUN — nessuna modifica verrà apportata ai file.")

    # -----------------------------------------------------------------------
    # Raccolta file
    # -----------------------------------------------------------------------
    print("\n📁 Raccolta file...")
    paths_a = collect_files(config.folder_a, config.supported_extensions)
    if config.single_mode:
        all_paths = paths_a
        n_img = sum(1 for p in all_paths if p.suffix.lower() in config.image_extensions)
        n_vid = len(all_paths) - n_img
        print(f"  Trovati {len(all_paths)} file ({n_img} foto, {n_vid} video)")
    else:
        paths_b = collect_files(config.folder_b, config.supported_extensions)
        all_paths = paths_a + paths_b
        n_img = sum(1 for p in all_paths if p.suffix.lower() in config.image_extensions)
        n_vid = len(all_paths) - n_img
        print(f"  Trovati {len(all_paths)} file ({n_img} foto, {n_vid} video) "
              f"— A: {len(paths_a)}, B: {len(paths_b)}")

    # -----------------------------------------------------------------------
    # Fase 1 — SHA-256
    # -----------------------------------------------------------------------
    print("\n🔐 Fase 1 — Deduplicazione esatta (SHA-256)...")
    unique_records, exact_dups, errors_sha = deduplicate_exact(all_paths, config, cache, _prog)
    print(f"\n  Unici: {len(unique_records)} | Duplicati esatti: {len(exact_dups)}")

    result = ScanResult(
        exact_duplicates=exact_dups,
        errors=errors_sha,
    )

    # -----------------------------------------------------------------------
    # Fase 2 — pHash (solo modalità due cartelle)
    # -----------------------------------------------------------------------
    if not config.single_mode:
        print("\n🔍 Fase 2 — Ricerca simili (pHash)...")
        unique_paths, similar_groups, errors_ph = deduplicate_perceptual(
            unique_records, config, cache, _prog
        )
        print(f"\n  Unici assoluti: {len(unique_paths)} | Gruppi simili: {len(similar_groups)}")
        result.unique = unique_paths
        result.similar_groups = similar_groups
        result.errors.extend(errors_ph)
    else:
        # In single mode i "unici" sono già i sopravvissuti alla fase SHA-256;
        # non vengono copiati da nessuna parte, restano in-place.
        result.unique = [r.path for r in unique_records]

    # -----------------------------------------------------------------------
    # Salva cache (dopo i calcoli, prima delle operazioni su disco)
    # -----------------------------------------------------------------------
    cache.save()

    # -----------------------------------------------------------------------
    # Operazioni su disco
    # -----------------------------------------------------------------------
    if config.single_mode:
        if exact_dups:
            proceed = confirm_delete(exact_dups) if confirm_delete else True
            if proceed:
                action = "Simulazione cancellazione" if config.dry_run else "Cancellazione duplicati esatti"
                print(f"\n🗑️  Fase 2 — {action}...")
                del_errors = delete_exact_duplicates(exact_dups, config.dry_run, _prog)
                print(f"\n  {'[DRY] ' if config.dry_run else ''}Elaborati: {len(exact_dups)} | Errori: {len(del_errors)}")
                result.errors.extend(del_errors)
            else:
                print("\n  ⚠️  Cancellazione annullata.")
        else:
            print("\n  Nessun duplicato esatto trovato.")

        if result.similar_groups:
            print("\n📂 Copia simili in /considering...")
            copy_results(result, config)
    else:
        print("\n📂 Copia file in output...")
        copy_results(result, config)

    # -----------------------------------------------------------------------
    # Report CSV
    # -----------------------------------------------------------------------
    print("\n📋 Scrittura report...")
    report_path = write_report(result, config)

    # -----------------------------------------------------------------------
    # Riepilogo finale
    # -----------------------------------------------------------------------
    print(f"\n{'✅' if not config.dry_run else '📋'} {'Completato!' if not config.dry_run else 'Dry run completato.'}")
    if config.single_mode:
        label = "[DRY] da cancellare" if config.dry_run else "cancellati"
        print(f"   Duplicati esatti {label} → {len(exact_dups)}")
        if result.similar_groups:
            print(f"   /considering          → {result.total_considering} file in {len(result.similar_groups)} gruppi")
    else:
        print(f"   /merged      → {result.total_merged} file")
        print(f"   /considering → {result.total_considering} file in {len(result.similar_groups)} gruppi")
    print(f"   Errori       → {len(result.errors)}")
    print(f"   Report       → {report_path}")

    return result
