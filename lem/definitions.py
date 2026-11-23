"""Which definitions make a good clue when the player must PRODUCE the word (sense → word, spelling cards, duels).

A clue is good if it points to one word. These do not:

  * a one-word definition (« Microphone. » for *micro*, « Chant. » for *hymne*): any synonym fits and the
    player writes the clue itself ;
  * a definition that starts with the word itself or with a longer form of it, i.e. a clipping or an
    abbreviation (*micro* → « Microphone d'un journaliste… », *chap.* → « Chapitre ») ;
  * a pointer to another word: « Abréviation de… », « Sigle de… », « Synonyme de… », « Variante de… » ;
  * a definition that contains the target word itself.

Recognition cards (word → sense) are not concerned: there the word is shown and every sense is welcome.
"""

from __future__ import annotations

import re

from lem.texte import normaliser

RE_RENVOI = re.compile(r"^(?:abr[ée]viation|apocope|aph[ée]r[èe]se|troncation|sigle|acronyme|synonyme|variante|"
                       r"autre orthographe|ancienne orthographe|forme|pluriel|f[ée]minin|masculin)\b"
                       r"(?:\s+(?:orthographique|graphique|familière|populaire))?\s*(?:de|du|des|d['’])", re.I)
RE_MOT = re.compile(r"[a-zà-ÿœæ]+", re.I)


def usable_as_clue(definition: str, form: str) -> bool:
    """True if `definition` can be shown as the clue for `form` without giving the answer away or leaving it open."""
    d = " ".join(definition.split()).strip()
    mots = RE_MOT.findall(d)
    if len(mots) <= 1 or RE_RENVOI.match(d):
        return False
    cible = normaliser(form).replace("-", "").replace("'", "")
    premier = normaliser(mots[0])
    if len(cible) >= 3 and (premier.startswith(cible) or (len(premier) >= 3 and cible.startswith(premier))):
        return False                                      # clipping : micro / microphone, chap / chapitre
    if cible in {normaliser(m) for m in mots} or (len(cible) >= 4 and cible in normaliser(d).replace("-", "")):
        return False                                      # the clue contains the answer
    return True


def first_usable_sense(senses: list[tuple[int, str]], form: str) -> tuple[int, str] | None:
    """The first (id, definition) of `senses` that is a good clue, or None."""
    return next(((i, d) for i, d in senses if usable_as_clue(d, form)), None)


# Racines et affixes savants (grec / latin, toujours en alphabet latin) pour l'éclairage étymologique court.
RACINES_SAVANTES: dict[str, str] = {
    "ortho-": "orthos « droit »",
    "-èdre": "hedra « face, base »",
    "-édrique": "hedra « face, base »",
    "acro-": "akros « extrémité, haut »",
    "-onyme": "onoma « nom »",
    "-onym": "onoma « nom »",
    "-onymie": "onoma « nom »",
    "pachy-": "pakhus « épais »",
    "-derme": "derma « peau »",
    "-dermique": "derma « peau »",
    "nyct-": "nux « nuit »",
    "nycto-": "nux « nuit »",
    "brachy-": "brakhus « court »",
    "-oure": "oura « queue »",
    "-ure": "oura « queue »",
    "tachy-": "takhus « rapide »",
    "brady-": "bradus « lent »",
    "-cardie": "kardia « cœur »",
    "-carde": "kardia « cœur »",
    "calli-": "kallos « beauté »",
    "caco-": "kakos « mauvais »",
    "-graphie": "graphein « écrire »",
    "-graphe": "graphein « écrire »",
    "biblio-": "biblion « livre »",
    "-phile": "philos « ami »",
    "-philie": "philos « ami »",
    "-phobe": "phobos « peur »",
    "-phobie": "phobos « peur »",
    "anthropo-": "anthropos « humain »",
    "-morphe": "morphe « forme »",
    "-morphie": "morphe « forme »",
    "chrono-": "khronos « temps »",
    "-logie": "logos « discours, étude »",
    "-logue": "logos « discours, étude »",
    "dys-": "dus- « difficulté »",
    "eu-": "eu « bien »",
    "iso-": "isos « égal »",
    "hétéro-": "heteros « autre »",
    "homo-": "homos « semblable »",
    "homéo-": "homoios « semblable »",
    "topo-": "topos « lieu »",
    "auto-": "autos « soi-même »",
    "télé-": "tele « loin »",
    "poly-": "polus « nombreux »",
    "mono-": "monos « seul »",
    "oligo-": "oligos « peu »",
    "pan-": "pas, pantos « tout »",
    "-archie": "arkhein « commander »",
    "-arque": "arkhein « commander »",
    "-cratie": "kratos « pouvoir »",
    "-crate": "kratos « pouvoir »",
    "-algie": "algos « douleur »",
    "-iatre": "iatros « médecin »",
    "-iatrie": "iatros « médecin »",
    "-scopie": "skopein « observer »",
    "-scope": "skopein « observer »",
    "-mètre": "metron « mesure »",
    "-métrie": "metron « mesure »",
    "-nome": "nomos « loi, règle »",
    "-nomie": "nomos « loi, règle »",
    "-gène": "genos « origine »",
    "-génie": "genos « origine »",
    "-vore": "vorare « dévorer »",
    "-cole": "colere « cultiver, habiter »",
    "-fère": "ferre « porter »",
    "-fuge": "fugere « fuir »",
    "-cide": "caedere « tuer »",
    "phyto-": "phuton « plante »",
    "zoo-": "zoon « animal »",
    "bio-": "bios « vie »",
    "nécro-": "nekros « mort »",
    "pyro-": "pur « feu »",
    "hydro-": "hudor « eau »",
    "géo-": "ge « terre »",
    "hélio-": "helios « soleil »",
    "litho-": "lithos « pierre »",
    "xéno-": "xenos « étranger »",
    "miso-": "misein « haïr »",
    "-gyne": "gune « femme »",
    "gynéco-": "gune « femme »",
    "andro-": "andros « homme »",
    "pédo-": "paidos « enfant »",
    "géronto-": "geron « vieillard »",
    "ostéo-": "osteon « os »",
    "myo-": "mus « muscle »",
    "neuro-": "neuron « nerf »",
    "hémato-": "haima « sang »",
    "hémo-": "haima « sang »",
    "-émie": "haima « sang »",
    "céphalo-": "kephale « tête »",
    "-céphale": "kephale « tête »",
    "rhino-": "rhinos « nez »",
    "oto-": "otos « oreille »",
    "ophtalmo-": "ops « œil, vue »",
    "-ope": "ops « vue, œil »",
    "-opie": "ops « vue, œil »",
    "gastro-": "gaster « estomac »",
    "hépato-": "hepar « foie »",
    "néphro-": "nephros « rein »",
    "pneumo-": "pneuma « souffle »",
    "psycho-": "psukhe « esprit »",
    "somato-": "soma « corps »",
    "théo-": "theos « dieu »",
    "cosmo-": "kosmos « monde, ordre »",
    "macro-": "makros « grand »",
    "micro-": "mikros « petit »",
    "méga-": "megas « grand »",
    "mégalo-": "megas « grand »",
    "néo-": "neos « nouveau »",
    "paléo-": "palaios « ancien »",
    "archéo-": "arkhaios « ancien »",
    "proto-": "protos « premier »",
    "pseudo-": "pseudos « faux »",
    "crypto-": "kruptos « caché »",
    "allo-": "allos « autre »",
    "méta-": "meta « après, changement »",
    "para-": "para « à côté, contre »",
    "péri-": "peri « autour »",
    "épi-": "epi « sur »",
    "hypo-": "hupo « sous »",
    "hyper-": "huper « au-dessus »",
    "anti-": "anti « contre »",
    "syn-": "sun « avec »",
    "sym-": "sun « avec »",
    "dia-": "dia « à travers »",
    "ana-": "ana « à rebours »",
    "cata-": "kata « de haut en bas »",
    "apo-": "apo « loin de »",
    "éco-": "oikos « maison, habitat »",
    "-tomie": "tome « coupe »",
    "-tome": "tome « coupe »",
    "-ectomie": "ektome « ablation »",
    "-pathie": "pathos « affection »",
    "-pathe": "pathos « affection »",
    "-thérapie": "therapeia « soin »",
    "-manie": "mania « folie, passion »",
    "-mane": "mania « folie, passion »",
    "-latrie": "latreia « culte »",
    "-lâtre": "latreia « culte »",
    "-doxe": "doxa « opinion »",
    "-doxie": "doxa « opinion »",
    "-esthésie": "aisthesis « sensation »",
    "-lyse": "lusis « dissolution »",
    "-genèse": "genesis « formation »",
    "-gone": "gonia « angle »",
}

_PREFIXES_SAVANTS = sorted((k for k in RACINES_SAVANTES if k.endswith("-")), key=len, reverse=True)
_SUFFIXES_SAVANTS = sorted((k for k in RACINES_SAVANTES if k.startswith("-")), key=len, reverse=True)
_RE_RENVOI_ETYM = re.compile(r"(?:[Dd]érivé de|[Vv]oyez)\s+([a-zà-ÿœæ-]+)", re.I)
_RE_COUPLE_ETYM = re.compile(
    r"([a-zA-ZÀ-ÿœæōūēīāáéíóúýhat_\-]+)\s*\(?\s*«\s*([^»]{1,45})\s*»\s*\)?"
)
_RE_NON_LATIN = re.compile(r"[^\x00-\x7fÀ-ɏḀ-ỿ«»’'“”–—…°]+")


def _nettoyer_latin(texte: str) -> str:
    """Retire les écritures non latines (grec, cyrillique…) pour ne garder que la translittération et les gloses."""
    s = _RE_NON_LATIN.sub(" ", texte)
    s = re.sub(r"\(\s*[,;:\s]*\)", "", s)
    s = re.sub(r",\s*,", ",", s)
    return " ".join(s.split()).strip(" ,;:")


def resume_etymologie(con, entree_id: int) -> str:
    """Résumé étymologique ultra-court (une ligne, alphabet latin uniquement) pour éclairer la composition du mot."""
    perso = con.execute(
        "SELECT contenu FROM mnemo WHERE entree_id = ? AND type = 'etymologie' ORDER BY id DESC LIMIT 1",
        (entree_id,),
    ).fetchone()
    if perso and perso[0].strip():
        return perso[0].strip()

    row = con.execute("SELECT forme, origine, etymologie FROM entree WHERE id = ?", (entree_id,)).fetchone()
    if not row:
        return ""
    forme, origine, etym = row["forme"].lower(), row["origine"] or "", row["etymologie"] or ""

    # 1. Morphèmes enregistrés + détection directe des préfixes/suffixes savants (ex. ortho- + -édrique).
    morphemes: list[tuple[str, str]] = []
    vus: set[str] = set()
    vus_gloses: set[str] = set()
    for m_forme, m_glose in con.execute(
        """SELECT m.forme, m.glose FROM entree_morpheme em
           JOIN morpheme m ON m.id = em.morpheme_id WHERE em.entree_id = ? ORDER BY em.position""",
        (entree_id,),
    ):
        glose = RACINES_SAVANTES.get(m_forme) or m_glose
        if not glose:
            r_g = con.execute(
                "SELECT glose FROM morpheme WHERE forme = ? AND glose != '' LIMIT 1", (m_forme,)
            ).fetchone()
            glose = r_g[0] if r_g else ""
        if m_forme not in vus and glose and glose not in vus_gloses and glose.lower() not in ("adjectif", "nom", "verbe"):
            vus.add(m_forme)
            vus_gloses.add(glose)
            morphemes.append((m_forme, glose))

    for pref in _PREFIXES_SAVANTS:
        rac = pref[:-1]
        glose = RACINES_SAVANTES[pref]
        if forme.startswith(rac) and len(forme) > len(rac) + 2 and pref not in vus and glose not in vus_gloses:
            vus.add(pref)
            vus_gloses.add(glose)
            morphemes.insert(0, (pref, glose))
            break
    for suf in _SUFFIXES_SAVANTS:
        rac = suf[1:]
        glose = RACINES_SAVANTES[suf]
        if forme.endswith(rac) and len(forme) > len(rac) + 2 and suf not in vus and glose not in vus_gloses:
            vus.add(suf)
            vus_gloses.add(glose)
            morphemes.append((suf, glose))
            break

    # 2. Si « Dérivé de X » ou « voyez X », regarder aussi l'étymologie de la base X.
    etym_cible = etym
    m_base = _RE_RENVOI_ETYM.search(etym)
    if m_base:
        base_mot = m_base.group(1).strip(".,;()")
        r_base = con.execute(
            "SELECT etymologie, origine FROM entree WHERE lower(forme) = ? AND etymologie IS NOT NULL AND etymologie != '' LIMIT 1",
            (base_mot.lower(),),
        ).fetchone()
        if r_base and len(r_base[0]) > len(etym):
            etym_cible = r_base[0]
            if not origine or origine == "francais":
                origine = r_base[1] or origine

    # 3. Extraire les couples translittération « glose » dans l'étymologie (ex. pakhus « épais » + derma « peau »).
    couples = []
    STOP = {"sens", "qui", "de", "du", "le", "la", "les", "un", "une", "en", "au", "aux", "par", "pour", "avec", "mot"}
    for m in _RE_COUPLE_ETYM.finditer(_nettoyer_latin(etym_cible)):
        racine, glose = m.group(1).strip("- ,;"), m.group(2).strip()
        if len(racine) >= 2 and racine.lower() not in STOP and racine.lower() != forme:
            item = f"{racine} « {glose} »"
            if item not in couples:
                couples.append(item)

    if len(morphemes) >= 2:
        return " + ".join(f"{mf} ({mg})" if "«" in mg else f"{mf} « {mg} »" for mf, mg in morphemes[:3])
    if couples:
        prefixe = f"{origine} : " if origine and origine not in ("francais",) else ""
        res = prefixe + " + ".join(couples[:3])
        if len(morphemes) == 1 and morphemes[0][0] not in res:
            mf, mg = morphemes[0]
            res += f" · {mf} ({mg})" if "«" in mg else f" · {mf} « {mg} »"
        return res[:115]
    if len(morphemes) == 1:
        mf, mg = morphemes[0]
        return f"{mf} ({mg})" if "«" in mg else f"{mf} « {mg} »"
    if etym_cible:
        premiere = _nettoyer_latin(etym_cible.split(".")[0])
        if 5 <= len(premiere) <= 110 and not premiere.lower().startswith("dérivé de"):
            return premiere
    return ""

