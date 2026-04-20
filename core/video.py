"""
video.py — Fingerprint percettivo per i file video.

Utilizza OpenCV per estrarre frame e imagehash per calcolarne il pHash.

Strategia a due livelli:
  1. video_phash()        → pHash del frame centrale (usato nel BK-Tree per la ricerca veloce)
  2. video_phashes_multi() → pHash di 5 frame distribuiti (usato per la verifica accurata)
"""

from __future__ import annotations
from pathlib import Path

import imagehash
from PIL import Image


# ---------------------------------------------------------------------------
# Estensioni supportate
# ---------------------------------------------------------------------------

VIDEO_EXTENSIONS: frozenset[str] = frozenset({
    ".mp4", ".mov", ".avi", ".mkv", ".m4v",
    ".wmv", ".flv", ".webm", ".3gp", ".mts", ".m2ts",
})

# Posizioni dei frame per la verifica multi-frame (frazione della durata totale)
_MULTI_FRAME_POSITIONS: list[float] = [0.1, 0.3, 0.5, 0.7, 0.9]


# ---------------------------------------------------------------------------
# Utilità OpenCV (import lazy per non obbligare chi non usa video)
# ---------------------------------------------------------------------------

def _open_capture(path: Path):
    """Apre un VideoCapture OpenCV, lancia RuntimeError se fallisce."""
    try:
        import cv2
    except ImportError as e:
        raise RuntimeError(
            "opencv-python non è installato. "
            "Esegui: pip install opencv-python"
        ) from e

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Impossibile aprire il video: {path}")
    return cap, cv2


def _frame_to_pil(frame, cv2) -> Image.Image:
    """Converte un frame OpenCV (BGR numpy array) in PIL Image RGB."""
    import numpy as np
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


# ---------------------------------------------------------------------------
# API pubblica
# ---------------------------------------------------------------------------

def video_info(path: Path) -> tuple[int, int, float | None]:
    """
    Restituisce (width, height, duration_seconds) del video.
    duration è None se non leggibile.
    """
    cap, cv2 = _open_capture(path)
    width  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps    = cap.get(cv2.CAP_PROP_FPS)
    n_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    cap.release()

    duration = (n_frames / fps) if fps > 0 and n_frames > 0 else None
    return width, height, duration


def _extract_frame_at(cap, cv2, position: float) -> Image.Image | None:
    """
    Estrae il frame alla posizione indicata (0.0–1.0 della durata totale).
    Restituisce None se l'estrazione fallisce.
    """
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    if total <= 0:
        return None
    idx = max(0, min(int(total * position), int(total) - 1))
    cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
    ret, frame = cap.read()
    return _frame_to_pil(frame, cv2) if ret else None


def video_phash(path: Path) -> object:
    """
    Calcola il pHash del frame centrale del video (posizione 0.5).
    Usato come chiave nel BK-Tree per la ricerca veloce.
    Restituisce un imagehash.ImageHash.
    """
    cap, cv2 = _open_capture(path)
    try:
        img = _extract_frame_at(cap, cv2, 0.5)
        if img is None:
            raise RuntimeError(f"Impossibile estrarre il frame centrale da: {path}")
        return imagehash.phash(img)
    finally:
        cap.release()


def video_phashes_multi(
    path: Path,
    positions: list[float] | None = None,
) -> list[object]:
    """
    Calcola i pHash di N frame distribuiti nel video.
    Default: 5 frame a 10%, 30%, 50%, 70%, 90%.
    Restituisce una lista di imagehash.ImageHash (può essere più corta di positions
    se alcuni frame non sono estraibili).
    """
    if positions is None:
        positions = _MULTI_FRAME_POSITIONS

    cap, cv2 = _open_capture(path)
    hashes: list[object] = []
    try:
        for pos in positions:
            img = _extract_frame_at(cap, cv2, pos)
            if img is not None:
                hashes.append(imagehash.phash(img))
    finally:
        cap.release()

    return hashes


def video_phash_distance(
    hashes_a: list[object],
    hashes_b: list[object],
) -> float:
    """
    Distanza media tra due sequenze di pHash video.
    Usa la lunghezza della sequenza più corta.
    Restituisce float('inf') se una delle liste è vuota.
    """
    if not hashes_a or not hashes_b:
        return float("inf")
    n = min(len(hashes_a), len(hashes_b))
    return sum(hashes_a[i] - hashes_b[i] for i in range(n)) / n
