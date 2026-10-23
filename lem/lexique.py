"""Index compacts du lexique, partagés par les jeux et gardés sur disque.

Sur la vraie base (550 k entrées, 2,3 M formes), des dictionnaires Python de chaînes coûtaient ≈ 1 Go et 5 s à
chaque ouverture de la Bombe. Ici :
  * les formes (minuscules, accents compris) et leurs normes (sans accents) dans deux automates `marisa-trie`
    (quelques dizaines de Mo, recherche exacte en microsecondes) ;
  * les liens forme ↔ entrée en tableaux numpy (format CSR) ;
  * pour chaque entrée, ses normes jointes par « \\x00 » dans un seul bloc de texte : la recherche des mots qui
    contiennent une suite de lettres (Bombe) est un simple parcours.
Construit une fois (≈ 20 s) dans ~/.lem/cache/<base>/lexique-<signature>/, rechargé ensuite en une fraction de
seconde ; refait si les tables entree/forme changent. Une base en mémoire (tests) est indexée en mémoire.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
from pathlib import Path

import marisa_trie
import numpy as np

SEP = "\x00"
VERSION = 2          # à incrémenter quand le contenu de l'index change : les anciens caches sont refaits


def _signature(con: sqlite3.Connection) -> str:
    f = con.execute("SELECT COUNT(*), TOTAL(rowid), MAX(rowid) FROM forme").fetchone()
    e = con.execute("SELECT COUNT(*), TOTAL(id), MAX(id), (SELECT COUNT(*) FROM entree WHERE type = 'nom_propre'), "
                    "TOTAL(zipf), COUNT(origine) FROM entree").fetchone()
    d = con.execute("SELECT (SELECT COUNT(*) FROM sens_domaine), (SELECT COUNT(*) FROM entree_morpheme), "
                    "(SELECT COUNT(*) FROM sens)").fetchone()
    return hashlib.blake2b(repr((VERSION, tuple(f), tuple(e), tuple(d))).encode(), digest_size=6).hexdigest()


class Lexique:
    def __init__(self, con: sqlite3.Connection):
        from lem.vecteurs import DOSSIER_CACHE, _chemin_base

        self.racine: Path | None = None        # dossier du cache sur disque (None : base en mémoire)
        base = _chemin_base(con)
        if not base:
            self._construire(con, None)
            return
        dossier = DOSSIER_CACHE / hashlib.blake2b(os.path.realpath(base).encode(), digest_size=4).hexdigest()
        racine = dossier / f"lexique-{_signature(con)}"
        if not (racine / "complet").exists():
            for ancien in dossier.glob("lexique-*"):
                shutil.rmtree(ancien, ignore_errors=True)
            self._construire(con, racine)
        self._charger(racine)
        self.racine = racine

    # -- construction ------------------------------------------------------------------------------
    def _construire(self, con: sqlite3.Connection, racine: Path | None) -> None:
        from lem.memory.knowledge import SQL_DOMAINES, SQL_MORPHS, bande_zipf
        from lem.texte import normaliser

        lignes_e = con.execute("SELECT id, type, forme, zipf, origine FROM entree ORDER BY id").fetchall()
        eids = np.array([r[0] for r in lignes_e], dtype=np.int64)
        types = [r[1] for r in lignes_e]
        # Dimensions de la carte de connaissance (cf. knowledge._dimensions), par position d'entrée.
        zipf = np.array([int(bande_zipf(r[3])[5:]) for r in lignes_e], dtype=np.int8)
        noms_origines = sorted({r[4] for r in lignes_e if r[4]})
        io = {o: i for i, o in enumerate(noms_origines)}
        origine = np.array([io[r[4]] if r[4] else -1 for r in lignes_e], dtype=np.int16)
        noms_dims: dict[str, int] = {}
        d_lig, d_dim = [], []
        for sql, prefixe in ((SQL_DOMAINES, "domaine:"), (SQL_MORPHS, "morph:")):
            for eid, nom in con.execute(sql):
                d_lig.append(eid)
                d_dim.append(noms_dims.setdefault(prefixe + nom, len(noms_dims)))
        d_lig = np.searchsorted(eids, np.array(d_lig, dtype=np.int64)).astype(np.int32)
        d_dim = np.array(d_dim, dtype=np.int32)
        lemmes = [normaliser(r[2]) for r in lignes_e]
        noms_types = sorted(set(types))
        code_type = np.array([noms_types.index(t) for t in types], dtype=np.int8)
        rows = con.execute("SELECT forme, norme, entree_id FROM forme ORDER BY entree_id, rowid").fetchall()
        formes = marisa_trie.Trie(r[0] for r in rows)
        normes = marisa_trie.Trie(r[1] for r in rows)
        fid = np.fromiter((formes[r[0]] for r in rows), dtype=np.int32, count=len(rows))
        nid = np.fromiter((normes[r[1]] for r in rows), dtype=np.int32, count=len(rows))
        ent = np.searchsorted(eids, np.fromiter((r[2] for r in rows), dtype=np.int64, count=len(rows))).astype(np.int32)
        # entrée → (formes, normes), dans l'ordre des lignes (déjà triées par entrée)
        e_ptr = np.zeros(len(eids) + 1, dtype=np.int64)
        np.add.at(e_ptr, ent + 1, 1)
        e_ptr = np.cumsum(e_ptr)
        # forme → entrées ; norme → formes
        o = np.argsort(fid, kind="stable")
        f_ptr = np.searchsorted(fid[o], np.arange(len(formes) + 1)).astype(np.int64)
        o2 = np.lexsort((fid, nid))
        n_ptr = np.searchsorted(nid[o2], np.arange(len(normes) + 1)).astype(np.int64)
        # textes des entrées : leurs normes jointes, un seul bloc
        textes = []
        for k in range(len(eids)):
            textes.append(SEP.join(r[1] for r in rows[e_ptr[k]:e_ptr[k + 1]]))
        bloc = "\x01".join(textes)
        self.formes, self.normes, self.eids, self.code_type, self.noms_types = formes, normes, eids, code_type, noms_types
        self.e_ptr, self.e_fid, self.e_nid = e_ptr, fid, nid
        self.f_ptr, self.f_ent = f_ptr, ent[o]
        n_fid = fid[o2]
        self.n_ptr, self.n_fid = n_ptr, n_fid
        self.textes = textes
        self.zipf, self.origine, self.noms_origines = zipf, origine, noms_origines
        self.d_lig, self.d_dim, self.noms_dims = d_lig, d_dim, list(noms_dims)
        self.lemmes = lemmes
        if racine is None:
            return
        tmp = racine.with_name(racine.name + ".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        formes.save(str(tmp / "formes.marisa"))
        normes.save(str(tmp / "normes.marisa"))
        np.savez(tmp / "tableaux.npz", eids=eids, code_type=code_type, e_ptr=e_ptr, e_fid=fid, e_nid=nid,
                 f_ptr=f_ptr, f_ent=self.f_ent, n_ptr=n_ptr, n_fid=n_fid, zipf=zipf, origine=origine,
                 d_lig=d_lig, d_dim=d_dim)
        (tmp / "types.txt").write_text("\n".join(noms_types), encoding="utf-8")
        (tmp / "origines.txt").write_text("\n".join(noms_origines), encoding="utf-8")
        (tmp / "dimensions.txt").write_text("\n".join(self.noms_dims), encoding="utf-8")
        (tmp / "lemmes.txt").write_text("\x01".join(lemmes), encoding="utf-8")
        (tmp / "textes.txt").write_text(bloc, encoding="utf-8")
        (tmp / "complet").write_text("")
        shutil.rmtree(racine, ignore_errors=True)
        tmp.rename(racine)

    def _charger(self, racine: Path) -> None:
        self.formes = marisa_trie.Trie().mmap(str(racine / "formes.marisa"))
        self.normes = marisa_trie.Trie().mmap(str(racine / "normes.marisa"))
        t = np.load(racine / "tableaux.npz")
        self.eids, self.code_type, self.e_ptr, self.e_fid, self.e_nid = (t["eids"], t["code_type"], t["e_ptr"],
                                                                         t["e_fid"], t["e_nid"])
        self.f_ptr, self.f_ent, self.n_ptr, self.n_fid = t["f_ptr"], t["f_ent"], t["n_ptr"], t["n_fid"]
        self.noms_types = (racine / "types.txt").read_text(encoding="utf-8").split("\n")
        self.zipf, self.origine, self.d_lig, self.d_dim = t["zipf"], t["origine"], t["d_lig"], t["d_dim"]
        lire = lambda nom, sep: (racine / nom).read_text(encoding="utf-8").split(sep)  # noqa: E731
        self.noms_origines = [x for x in lire("origines.txt", "\n") if x]
        self.noms_dims = [x for x in lire("dimensions.txt", "\n") if x]
        self.lemmes = lire("lemmes.txt", "\x01")
        self.textes = (racine / "textes.txt").read_text(encoding="utf-8").split("\x01")

    # -- lecture -----------------------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.eids)

    def position(self, entree_id: int) -> int | None:
        k = int(np.searchsorted(self.eids, entree_id))
        return k if k < len(self.eids) and self.eids[k] == entree_id else None

    def type_de(self, k: int) -> str:
        return self.noms_types[self.code_type[k]]

    def entrees(self, forme: str, exclus: tuple[str, ...] = ()) -> tuple[int, ...]:
        """Entrées dont `forme` (minuscules) est une forme exacte, hors types exclus."""
        i = self.formes.get(forme)
        if i is None:
            return ()
        ks = self.f_ent[self.f_ptr[i]:self.f_ptr[i + 1]]
        return tuple(dict.fromkeys(int(self.eids[k]) for k in ks if not exclus or self.type_de(k) not in exclus))

    def formes_de_norme(self, norme: str) -> list[str]:
        i = self.normes.get(norme)
        if i is None:
            return []
        return list(dict.fromkeys(self.formes.restore_key(int(f)) for f in self.n_fid[self.n_ptr[i]:self.n_ptr[i + 1]]))

    def formes_et_normes(self, k: int) -> list[tuple[str, str]]:
        """(forme, norme) de l'entrée en position k."""
        a, b = self.e_ptr[k], self.e_ptr[k + 1]
        return [(self.formes.restore_key(int(f)), self.normes.restore_key(int(n)))
                for f, n in zip(self.e_fid[a:b], self.e_nid[a:b])]

    def texte(self, k: int) -> str:
        return self.textes[k]

    def normes_contenant(self, suite: str, longueurs: range) -> list[str]:
        """Normes qui contiennent `suite` et dont la longueur est dans `longueurs`. Les normes sont rangées par
        longueur en blocs de texte (construits à la première demande, ≈ 1 s) : une expression régulière par bloc."""
        if not hasattr(self, "_par_longueur"):
            groupes: dict[int, list[str]] = {}
            for n in self.normes.iterkeys():
                groupes.setdefault(len(n), []).append(n)
            self._par_longueur = {L: SEP + SEP.join(ns) + SEP for L, ns in groupes.items()}
        motif = re.compile(f"(?<={SEP})[^{SEP}]*{re.escape(suite)}[^{SEP}]*(?={SEP})")
        return [m.group() for L in longueurs for m in motif.finditer(self._par_longueur.get(L, ""))]
