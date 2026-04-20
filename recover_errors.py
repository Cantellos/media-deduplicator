"""
recover_errors.py — Recupera i file segnalati come errori nel report CSV.

Durante una scansione, alcuni file possono risultare illeggibili (corruzione
parziale, permessi, formato non supportato, ecc.) e vengono registrati nel
CSV con tipo="errore". Questo script li copia tutti in una cartella di
destinazione scelta, così puoi tenerli al sicuro e ispezionarli manualmente.

Utilizzo:
    python recover_errors.py --report "C:/foto/report_20240101.csv" --out "C:/foto/recuperati"

Opzioni:
    --report   Percorso al file CSV generato dallo scanner (obbligatorio)
    --out      Cartella di destinazione (verrà creata se non esiste, obbligatorio)
    --also     Tipi aggiuntivi da recuperare oltre agli errori.
               Valori possibili: unico, duplicato_esatto, simile
               Esempio: --also simile duplicato_esatto
               Default: solo gli errori
"""

import argparse
import csv
import shutil
import sys
from pathlib import Path


VALID_TYPES = {"unico", "duplicato_esatto", "simile", "errore"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recupera i file con errori dal report CSV dello scanner.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--report", required=True, metavar="CSV",
                        help="Percorso al file CSV del report")
    parser.add_argument("--out", required=True, metavar="CARTELLA",
                        help="Cartella di destinazione per i file recuperati")
    parser.add_argument("--also", nargs="*", metavar="TIPO", default=[],
                        choices=list(VALID_TYPES - {"errore"}),
                        help="Tipi aggiuntivi da copiare (unico, duplicato_esatto, simile)")
    return parser.parse_args()


def safe_copy(src: Path, dest_dir: Path) -> Path:
    """Copia src in dest_dir; in caso di conflitto aggiunge il nome della cartella padre."""
    dest = dest_dir / src.name
    if dest.exists():
        # Usa il nome della cartella padre per disambiguare
        dest = dest_dir / f"{src.parent.name}__{src.name}"
    if dest.exists():
        # Caso estremo: aggiunge anche la dimensione in byte
        dest = dest_dir / f"{src.parent.name}__{src.stem}__{src.stat().st_size}{src.suffix}"
    shutil.copy2(src, dest)
    return dest


def load_report(report_path: Path, target_types: set[str]) -> list[Path]:
    """
    Legge il CSV e restituisce i percorsi di file_principale per le righe
    il cui tipo è in target_types.
    """
    paths: list[Path] = []
    skipped_types: dict[str, int] = {}

    with open(report_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        # Verifica che il CSV abbia le colonne attese
        expected = {"tipo", "file_principale"}
        if not expected.issubset(set(reader.fieldnames or [])):
            print(f"❌ Il file CSV non sembra un report valido.")
            print(f"   Colonne trovate : {reader.fieldnames}")
            print(f"   Colonne attese  : {sorted(expected)}")
            sys.exit(1)

        for row in reader:
            tipo = row["tipo"].strip()
            path_str = row["file_principale"].strip()
            if tipo in target_types:
                if path_str:
                    paths.append(Path(path_str))
            elif tipo not in VALID_TYPES:
                skipped_types[tipo] = skipped_types.get(tipo, 0) + 1

    if skipped_types:
        print(f"  ⚠️  Tipi non riconosciuti nel CSV (ignorati): {skipped_types}")

    return paths


def main():
    print("=" * 60)
    print("  Recover Errors — recupero file da report CSV")
    print("=" * 60)

    args = parse_args()
    report_path = Path(args.report)
    out_dir = Path(args.out)

    # Validazione input
    errors = []
    if not report_path.exists():
        errors.append(f"Report non trovato: {report_path}")
    if not report_path.suffix.lower() == ".csv":
        errors.append(f"Il file indicato non è un CSV: {report_path}")
    if errors:
        for e in errors:
            print(f"❌ {e}")
        sys.exit(1)

    # Tipi da recuperare
    target_types = {"errore"} | set(args.also)
    print(f"\n  Report  : {report_path}")
    print(f"  Output  : {out_dir}")
    print(f"  Tipi    : {', '.join(sorted(target_types))}")

    # Lettura CSV
    print(f"\n📋 Lettura report...")
    paths = load_report(report_path, target_types)

    if not paths:
        print(f"\n  Nessun file trovato con tipo: {', '.join(sorted(target_types))}")
        print("  Nulla da fare.")
        sys.exit(0)

    print(f"  Trovati {len(paths)} file da recuperare.")

    # Verifica esistenza dei file sorgente
    found = [p for p in paths if p.exists()]
    missing = [p for p in paths if not p.exists()]

    if missing:
        print(f"\n  ⚠️  {len(missing)} file non più reperibili sul disco:")
        for p in missing[:10]:
            print(f"     {p}")
        if len(missing) > 10:
            print(f"     ... e altri {len(missing) - 10}.")

    if not found:
        print("\n❌ Nessun file recuperabile trovato sul disco.")
        sys.exit(1)

    print(f"\n  File recuperabili  : {len(found)}")
    print(f"  File non trovati   : {len(missing)}")
    print()
    input("  Premi INVIO per copiare, CTRL+C per annullare... ")

    # Copia
    out_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    copy_errors: list[tuple[Path, str]] = []

    for i, src in enumerate(found, start=1):
        pct = int(i / len(found) * 100)
        print(f"  [{pct:3d}%] {src.name}", end="\r")
        try:
            safe_copy(src, out_dir)
            copied += 1
        except Exception as e:
            copy_errors.append((src, str(e)))

    print()  # pulisce la riga del progresso

    # Riepilogo
    print(f"\n✅ Completato!")
    print(f"   Copiati      → {copied} file in {out_dir}")
    if missing:
        print(f"   Non trovati  → {len(missing)} file (percorso non più valido)")
    if copy_errors:
        print(f"   Errori copia → {len(copy_errors)} file:")
        for p, err in copy_errors:
            print(f"     {p}: {err}")


if __name__ == "__main__":
    main()
