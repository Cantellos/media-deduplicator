"""
cache.py — Cache persistente degli hash su disco.

Evita di ricalcolare SHA-256 e fingerprint percettivi su file già processati.
La cache è indicizzata per (percorso_assoluto, mtime_ns, dimensione_bytes):
se uno di questi tre valori cambia, il file viene ricalcolato automaticamente.

Il file di cache è un JSON salvato nella cartella sorgente principale.
"""

import json
from pathlib import Path


class HashCache:
    """
    Cache in-memory con persistenza su JSON.

    Struttura interna:
    {
        "<path>|<mtime_ns>|<size>": {
            "sha256": "abc...",
            "phash":  "f8a0...",          # immagini e video (frame centrale)
            "vphashes": ["f8a0..", ...]   # video: lista pHash multi-frame
        },
        ...
    }
    """

    def __init__(self, cache_path: Path | None = None):
        self._path = cache_path
        self._data: dict[str, dict] = {}
        self._dirty = False

        if cache_path and cache_path.exists():
            try:
                with open(cache_path, encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception:
                # Cache corrotta o formato non valido: riparte da zero
                self._data = {}

    # ------------------------------------------------------------------
    # Chiave di cache
    # ------------------------------------------------------------------

    def _key(self, path: Path) -> str:
        stat = path.stat()
        return f"{path.resolve()}|{stat.st_mtime_ns}|{stat.st_size}"

    # ------------------------------------------------------------------
    # Lettura
    # ------------------------------------------------------------------

    def get_sha256(self, path: Path) -> str | None:
        return self._data.get(self._key(path), {}).get("sha256")

    def get_phash(self, path: Path) -> str | None:
        """pHash singolo (immagini) o frame centrale (video)."""
        return self._data.get(self._key(path), {}).get("phash")

    def get_video_phashes(self, path: Path) -> list[str] | None:
        """Lista pHash multi-frame per video. None se non in cache."""
        return self._data.get(self._key(path), {}).get("vphashes")

    # ------------------------------------------------------------------
    # Scrittura
    # ------------------------------------------------------------------

    def set_sha256(self, path: Path, sha256: str) -> None:
        self._data.setdefault(self._key(path), {})["sha256"] = sha256
        self._dirty = True

    def set_phash(self, path: Path, phash_str: str) -> None:
        self._data.setdefault(self._key(path), {})["phash"] = phash_str
        self._dirty = True

    def set_video_phashes(self, path: Path, phash_strs: list[str]) -> None:
        self._data.setdefault(self._key(path), {})["vphashes"] = phash_strs
        self._dirty = True

    # ------------------------------------------------------------------
    # Persistenza
    # ------------------------------------------------------------------

    def save(self) -> None:
        """Salva la cache su disco solo se ci sono modifiche."""
        if not self._path or not self._dirty:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, separators=(",", ":"))
            tmp.replace(self._path)   # atomic replace
        except Exception:
            if tmp.exists():
                tmp.unlink()

    def __len__(self) -> int:
        return len(self._data)
