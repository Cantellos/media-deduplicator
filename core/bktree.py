"""
BK-Tree per la ricerca efficiente di pHash simili.

Permette di trovare tutte le immagini con distanza di Hamming ≤ soglia
in O(log n) invece di O(n²), fondamentale con 10k-50k file.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BKNode:
    phash: object          # imagehash.ImageHash
    path: Path
    children: dict = field(default_factory=dict)   # distanza → figlio


class BKTree:
    """
    BK-Tree specializzato per distanza di Hamming su pHash.
    Insert: O(log n) amortizzato
    Search (range): O(log n) amortizzato
    """

    def __init__(self):
        self.root: BKNode | None = None
        self._size: int = 0

    def insert(self, phash: object, path: Path) -> None:
        node = BKNode(phash=phash, path=path)
        if self.root is None:
            self.root = node
            self._size += 1
            return

        current = self.root
        while True:
            dist = phash - current.phash   # distanza di Hamming (op. di imagehash)
            if dist == 0:
                # Hash identico: non inserire (già gestito da SHA-256 a monte,
                # ma può capitare con immagini visivamente uguali ma byte diversi)
                return
            if dist not in current.children:
                current.children[dist] = node
                self._size += 1
                return
            current = current.children[dist]

    def search(self, phash: object, threshold: int) -> list[tuple[int, Path]]:
        """
        Restituisce tutti i nodi con distanza di Hamming ≤ threshold.
        Output: lista di (distanza, path) ordinata per distanza crescente.
        """
        if self.root is None:
            return []

        results: list[tuple[int, Path]] = []
        stack = [self.root]

        while stack:
            node = stack.pop()
            dist = phash - node.phash

            if dist <= threshold:
                results.append((dist, node.path))

            # Esplora i figli nel range [dist - threshold, dist + threshold]
            lo = max(0, dist - threshold)
            hi = dist + threshold
            for child_dist, child in node.children.items():
                if lo <= child_dist <= hi:
                    stack.append(child)

        return sorted(results, key=lambda x: x[0])

    def __len__(self) -> int:
        return self._size
