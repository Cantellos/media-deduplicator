"""
main.py — Interfaccia da terminale.
La GUI futura importerà core.scanner.run_scan con la stessa interfaccia.

────────────────────────────────────────────────────
MODALITÀ CARTELLA SINGOLA  (--single)
────────────────────────────────────────────────────
Lavora in-place su una sola cartella (foto e video).
Cancella fisicamente i duplicati esatti (SHA-256 identico = pixel identici).
I file simili ma non identici vengono copiati in --out/considering per revisione.

    python main.py --single "C:/media/archivio" --out "C:/media/report"

────────────────────────────────────────────────────
MODALITÀ DUE CARTELLE  (--a / --b)
────────────────────────────────────────────────────
Unisce due cartelle eliminando i doppioni, senza toccare gli originali.
Duplicati esatti → /merged (copia migliore).
Simili (pHash) → /considering per revisione manuale.
Funziona sia per foto che per video.

    python main.py --a "C:/media/backup1" --b "C:/media/backup2" --out "C:/media/risultato"

────────────────────────────────────────────────────
OPZIONI COMUNI
────────────────────────────────────────────────────
  --dry-run     Simula tutto senza modificare nulla su disco.
                Scrive comunque il report CSV con suffisso _dryrun.
  --threshold   Soglia pHash per /considering (default: 10).
                  ≤ 5  → solo quasi-identici
                   10  → default (crop lievi, resize)
                  15+  → più aggressivo
"""

import argparse
import sys
from pathlib import Path

from core.models import ScanConfig
from core.scanner import run_scan


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Media Deduplicator — elimina i doppioni da una o due cartelle (foto e video).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--single", metavar="CARTELLA",
        help="Modalità in-place: scansiona una cartella e cancella i duplicati esatti",
    )

    two = parser.add_argument_group("modalità due cartelle")
    two.add_argument("--a", metavar="CARTELLA_A", help="Prima cartella sorgente")
    two.add_argument("--b", metavar="CARTELLA_B", help="Seconda cartella sorgente")

    parser.add_argument(
        "--out", required=True, metavar="OUTPUT",
        help="Cartella di output per report e /considering (deve essere vuota o non esistente)",
    )
    parser.add_argument(
        "--threshold", type=int, default=10,
        help="Soglia pHash per /considering (default: 10)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Simula tutto senza modificare nulla su disco",
    )
    return parser.parse_args()


def validate(args: argparse.Namespace) -> ScanConfig:
    output_dir = Path(args.out)
    errors = []

    # La cartella di output può contenere solo il file di cache
    if output_dir.exists():
        non_cache = [p for p in output_dir.iterdir() if p.name != ".dedup_cache.json"]
        if non_cache:
            errors.append(
                f"La cartella di output '{output_dir}' esiste già e non è vuota.\n"
                "  Scegli una cartella vuota o non esistente per evitare sovrascritture."
            )

    if not (0 < args.threshold <= 64):
        errors.append("--threshold deve essere tra 1 e 64")

    if args.single:
        folder = Path(args.single)
        if not folder.exists():
            errors.append(f"Cartella non trovata: {folder}")
        if errors:
            _print_errors(errors)
        # Cache nella cartella sorgente, così viene riutilizzata tra run diversi
        cache_file = folder / ".dedup_cache.json"
        return ScanConfig(
            folder_a=folder,
            folder_b=None,
            output_dir=output_dir,
            single_mode=True,
            dry_run=args.dry_run,
            phash_threshold=args.threshold,
            cache_file=cache_file,
        )
    else:
        if not args.a or not args.b:
            errors.append("In modalità due cartelle sono richiesti sia --a che --b")
        else:
            folder_a, folder_b = Path(args.a), Path(args.b)
            if not folder_a.exists():
                errors.append(f"Cartella A non trovata: {folder_a}")
            if not folder_b.exists():
                errors.append(f"Cartella B non trovata: {folder_b}")
        if errors:
            _print_errors(errors)
        # Cache nella prima cartella sorgente
        cache_file = folder_a / ".dedup_cache.json"
        return ScanConfig(
            folder_a=folder_a,
            folder_b=folder_b,
            output_dir=output_dir,
            single_mode=False,
            dry_run=args.dry_run,
            phash_threshold=args.threshold,
            cache_file=cache_file,
        )


def _print_errors(errors: list[str]) -> None:
    for e in errors:
        print(f"❌ {e}")
    sys.exit(1)


def confirm_delete(pairs: list) -> bool:
    """
    Mostra un'anteprima dei file che verranno cancellati e chiede conferma esplicita.
    Restituisce True se l'utente digita CANCELLA, False altrimenti.
    """
    print(f"\n  ⚠️  ATTENZIONE — OPERAZIONE IRREVERSIBILE")
    print(f"  Stanno per essere cancellati {len(pairs)} file duplicati dalla cartella sorgente.")
    print(f"  Per ogni coppia viene tenuto il file con risoluzione maggiore.\n")

    preview = pairs[:10]
    for pair in preview:
        print(f"  🗑️  {pair.skipped}")
        print(f"     ✅ tenuto: {pair.kept.name}")
    if len(pairs) > 10:
        print(f"  ... e altri {len(pairs) - 10} file.")

    print()
    risposta = input(
        "  Digita CANCELLA e premi INVIO per procedere, oppure solo INVIO per annullare: "
    ).strip()
    return risposta == "CANCELLA"


def main():
    print("=" * 60)
    print("  Media Deduplicator")
    print("=" * 60)

    args = parse_args()
    config = validate(args)

    # Riepilogo parametri
    if config.single_mode:
        print(f"\n  Modalità      : cartella singola (in-place)")
        print(f"  Cartella      : {config.folder_a}")
        print(f"  Output        : {config.output_dir}")
        print(f"\n  ⚠️  I duplicati esatti (SHA-256) verranno CANCELLATI dalla cartella sorgente.")
        print(f"  I file simili (pHash) verranno copiati in /considering per la revisione.")
        print(f"  Gli originali unici NON verranno toccati.")
    else:
        print(f"\n  Modalità      : due cartelle")
        print(f"  Cartella A    : {config.folder_a}")
        print(f"  Cartella B    : {config.folder_b}")
        print(f"  Output        : {config.output_dir}")
        print(f"  Soglia pHash  : {config.phash_threshold}")
        print(f"\n  ⚠️  Le cartelle originali NON verranno modificate.")
        print(f"  I file verranno COPIATI in output.")

    if config.dry_run:
        print(f"\n  🔍 DRY RUN attivo — nessun file verrà modificato o cancellato.")

    print()
    input("  Premi INVIO per avviare la scansione, CTRL+C per annullare... ")

    run_scan(
        config,
        confirm_delete=confirm_delete if (config.single_mode and not config.dry_run) else None,
    )


if __name__ == "__main__":
    main()
