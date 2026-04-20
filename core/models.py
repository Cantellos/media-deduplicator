"""
Strutture dati condivise tra scanner e GUI (futura).
"""

from dataclasses import dataclass, field
from pathlib import Path
from enum import Enum

from core.video import VIDEO_EXTENSIONS


# ---------------------------------------------------------------------------
# Estensioni supportate
# ---------------------------------------------------------------------------

IMAGE_EXTENSIONS: frozenset[str] = frozenset({".jpg", ".jpeg", ".png"})

ALL_EXTENSIONS: frozenset[str] = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS


# ---------------------------------------------------------------------------
# Enumerazioni
# ---------------------------------------------------------------------------

class Decision(Enum):
    MERGED = "merged"
    CONSIDERING = "considering"
    DUPLICATE_SKIPPED = "duplicate_skipped"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Record per un singolo file
# ---------------------------------------------------------------------------

@dataclass
class ImageRecord:
    path: Path
    sha256: str
    phash: object          # imagehash.ImageHash — per immagini: pHash diretto;
                           #                       per video: pHash del frame centrale
    width: int
    height: int
    size_bytes: int
    is_video: bool = False
    duration: float | None = None   # durata in secondi (solo per video)

    @property
    def resolution(self) -> int:
        return self.width * self.height


# ---------------------------------------------------------------------------
# Coppie e gruppi di duplicati
# ---------------------------------------------------------------------------

@dataclass
class DuplicatePair:
    kept: Path             # copia tenuta (risoluzione maggiore)
    skipped: Path          # copia scartata
    method: str            # "sha256" o "phash"
    phash_distance: int = 0


@dataclass
class SimilarGroup:
    best: Path             # file a risoluzione maggiore → va in /merged
    others: list[Path]     # simili → vanno in /considering
    phash_distance: float  # distanza massima nel gruppo (int per foto, float per video)


# ---------------------------------------------------------------------------
# Risultato aggregato della scansione
# ---------------------------------------------------------------------------

@dataclass
class ScanResult:
    # File unici: vanno in /merged (modalità due cartelle)
    unique: list[Path] = field(default_factory=list)

    # Duplicati esatti (SHA-256): solo il migliore va in /merged o viene tenuto in-place
    exact_duplicates: list[DuplicatePair] = field(default_factory=list)

    # Simili (pHash): il migliore in /merged, gli altri in /considering
    similar_groups: list[SimilarGroup] = field(default_factory=list)

    # File che non è stato possibile leggere
    errors: list[tuple[Path, str]] = field(default_factory=list)

    # Contatori comodi per progress e report
    @property
    def total_merged(self) -> int:
        return len(self.unique) + len(self.exact_duplicates) + len(self.similar_groups)

    @property
    def total_considering(self) -> int:
        return sum(len(g.others) for g in self.similar_groups)

    @property
    def total_skipped(self) -> int:
        return len(self.exact_duplicates)


# ---------------------------------------------------------------------------
# Configurazione della scansione
# ---------------------------------------------------------------------------

@dataclass
class ScanConfig:
    folder_a: Path
    output_dir: Path
    folder_b: Path | None = None        # None in modalità --single

    # Modalità operative
    single_mode: bool = False           # True → in-place, cancella duplicati esatti, no pHash
    dry_run: bool = False               # True → nessuna operazione su disco, solo report

    # Parametri di confronto
    phash_threshold: int = 10           # distanza pHash massima per /considering
    duration_tolerance: float = 0.05    # tolleranza sulla durata video (5 %)

    # Cache
    cache_file: Path | None = None      # None → cache disabilitata

    # Estensioni
    supported_extensions: frozenset = field(default_factory=lambda: ALL_EXTENSIONS)
    image_extensions: frozenset = field(default_factory=lambda: IMAGE_EXTENSIONS)
    video_extensions: frozenset = field(default_factory=lambda: VIDEO_EXTENSIONS)
