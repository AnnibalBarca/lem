"""Génère des réponses d'apprenant (francophone natif qui joue vite) pour chaque sens de l'échantillon.

    python scripts/distillation/generer_reponses.py [--limite N] [--modele qwen3:8b]

Générateur : un LLM local servi par Ollama (le GPU du PC, sans toucher au quota Claude). Chaque sens reçoit
4 réponses d'intentions tirées pour équilibrer les types d'erreur : une juste, une partielle, une fausse et une
au hasard. L'intention du générateur n'est PAS l'étiquette : c'est le professeur (Sonnet) qui juge ensuite.
Écrit DONNEES/reponses.jsonl au fil de l'eau ; reprend où il s'est arrêté.
"""

from __future__ import annotations

import argparse
import json
import random
import time

import httpx

from commun import DONNEES, ajouter_jsonl, lire_jsonl

JUSTES = {
    "paraphrase": "une définition juste, dans tes propres mots, sans recopier la définition de référence",
    "synonymes": "deux ou trois synonymes justes, séparés par des virgules",
    "juste_avec_fautes": "une définition juste mais tapée vite, avec une ou deux fautes de frappe ou d'accent",
}
PARTIELLES = {
    "trop_generale": "une définition trop générale : la catégorie (une sorte de…) sans le trait distinctif",
    "trop_specifique": "une définition trop étroite : un cas particulier pris pour le tout",
    "connotation_seule": "seulement l'impression ou la tonalité du mot (péjoratif, triste, savant…), pas son sens",
    "contexte_seul": "seulement où ou quand on l'emploie (en cuisine, au Moyen Âge…), pas ce qu'il veut dire",
    "juste_imprecise": "la bonne idée mais floue, incomplète, maladroite",
}
FAUSSES = {
    "confusion_formelle": "le sens du mot qui lui RESSEMBLE par la forme : {voisin_forme}",
    "sens_voisin": "le sens d'un mot VOISIN par le sens : {voisin_sens}",
    "autre_sens": "un AUTRE sens réel de ce mot (pas celui visé) : {autre_sens}",
    "contresens": "un contresens franc, ou quelque chose sans rapport, dit avec assurance",
}
VIDES = ["je ne sais pas", "aucune idée", "?", "jsp", "je sais plus", "rien", "euh"]

CONSIGNE = """Tu fabriques des réponses d'apprenants pour un jeu de vocabulaire français. Un francophone natif
voit un MOT et tape ce qu'il croit être son sens, vite, au clavier, comme dans un jeu : souvent quelques mots,
parfois une phrase courte, parfois des synonymes. Ne recopie jamais la définition de référence. Varie le
registre et la longueur (2 à 20 mots). Écris en français.

Pour chaque intention demandée, écris UNE réponse qui la réalise vraiment. Réponds en JSON :
{"reponses": [{"intention": "...", "texte": "..."}]}"""

SCHEMA = {"type": "object", "properties": {"reponses": {"type": "array", "items": {
    "type": "object", "properties": {"intention": {"type": "string"}, "texte": {"type": "string"}},
    "required": ["intention", "texte"]}}}, "required": ["reponses"]}


def intentions(item: dict, rng: random.Random) -> list[tuple[str, str]]:
    """4 intentions : juste, partielle, fausse, et une au hasard (parmi celles que l'item permet)."""
    forme = [v for v in item["voisins"] if v["nature"] == "forme" and v.get("definition")]
    sens = [v for v in item["voisins"] if v["nature"] == "sens" and v.get("definition")]
    autres = [s for s in item["sens"] if s["id"] != item["cible"]["id"]]
    fausses = {"contresens": FAUSSES["contresens"]}
    if forme:
        v = rng.choice(forme)
        fausses["confusion_formelle"] = FAUSSES["confusion_formelle"].format(voisin_forme=f"{v['mot']} ({v['definition']})")
    if sens:
        v = rng.choice(sens)
        fausses["sens_voisin"] = FAUSSES["sens_voisin"].format(voisin_sens=f"{v['mot']} ({v['definition']})")
    if autres:
        fausses["autre_sens"] = FAUSSES["autre_sens"].format(autre_sens=rng.choice(autres)["definition"])
    tous = {**JUSTES, **PARTIELLES, **fausses}
    choix = [rng.choice(list(JUSTES)), rng.choice(list(PARTIELLES)), rng.choice(list(fausses))]
    choix.append(rng.choice([k for k in tous if k not in choix]))
    return [(k, tous[k]) for k in choix]


def generer(item: dict, demandes: list[tuple[str, str]], modele: str, url: str) -> list[dict]:
    corps = {
        "model": modele,
        "messages": [
            {"role": "system", "content": CONSIGNE},
            {"role": "user", "content": json.dumps({
                "mot": item["mot"], "categorie": item["categorie"],
                "definition_de_reference": item["cible"]["definition"],
                "intentions": [{"intention": k, "consigne": c} for k, c in demandes]}, ensure_ascii=False)},
        ],
        "format": SCHEMA, "stream": False, "think": False,
        "options": {"temperature": 0.9}, "keep_alive": "30m",
    }
    r = httpx.post(f"{url}/api/chat", json=corps, timeout=120)
    r.raise_for_status()
    sortie = json.loads(r.json()["message"]["content"]).get("reponses", [])
    voulues = {k for k, _ in demandes}
    return [x for x in sortie if x.get("intention") in voulues and (x.get("texte") or "").strip()]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--modele", default="qwen3:8b")
    ap.add_argument("--url", default="http://localhost:11434")
    ap.add_argument("--limite", type=int, default=None)
    ap.add_argument("--graine", type=int, default=1)
    ap.add_argument("--tranche", default="0/1", help="k/n : seulement les sens d'indice ≡ k (mod n), pour paralléliser")
    a = ap.parse_args()
    items = lire_jsonl(DONNEES / "echantillon.jsonl")
    sortie = DONNEES / "reponses.jsonl"
    faits = {o["sens_id"] for o in lire_jsonl(sortie)}
    k_tranche, n_tranches = (int(x) for x in a.tranche.split("/"))
    items = [it for i, it in enumerate(items) if i % n_tranches == k_tranche]
    rng = random.Random(a.graine * 1000 + k_tranche)
    t0, n = time.time(), 0
    for item in items:
        if a.limite is not None and n >= a.limite:
            break
        if item["cible"]["id"] in faits:
            continue
        demandes = intentions(item, rng)
        try:
            reps = generer(item, demandes, a.modele, a.url)
        except (httpx.HTTPError, json.JSONDecodeError, KeyError) as exc:
            print(f"  {item['mot']} : échec ({exc}) — repris au prochain passage", flush=True)
            continue
        lignes = [{"sens_id": item["cible"]["id"], "entree_id": item["entree_id"], "mot": item["mot"],
                   "intention": x["intention"], "reponse": x["texte"].strip(), "generateur": a.modele} for x in reps]
        if rng.random() < 0.05:                     # quelques réponses vides, comme en vrai
            lignes.append({"sens_id": item["cible"]["id"], "entree_id": item["entree_id"], "mot": item["mot"],
                           "intention": "vide", "reponse": rng.choice(VIDES), "generateur": "gabarit"})
        ajouter_jsonl(sortie, lignes)
        n += 1
        if n % 100 == 0:
            dt = time.time() - t0
            print(f"  {n} sens · {dt / n:.2f} s/sens · {len(faits) + n}/{len(items)}", flush=True)
    print(f"fait : {n} sens en {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
