"""
Compare le score « mer de nuages » de l'outil aux étiquettes issues des webcams.

Usage : python outils/evaluer.py [camera=pdd] [moment=lever]
Lit etiquetage/previsions.csv et etiquetage/etiquettes.csv ; affiche, pour plusieurs seuils,
le nombre de mers de nuages vues (M) détectées et de fausses alertes, avec la pondération
des modèles de config.json.
"""
import collections
import csv
import json
import os
import sys

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VALLEES = {"pdd": ["limagne_nord", "veyre", "sioule"],
           "montdore": ["limagne_nord", "veyre", "limagne_sud", "chambon", "dordogne", "sioule", "ouest"]}


def main(cam="pdd", moment="lever"):
    cfg = json.load(open(os.path.join(RACINE, "config.json"), encoding="utf-8"))
    poids = cfg.get("poids_modeles", {})
    etiq = {r["date"]: r["classe"] for r in csv.DictReader(open(os.path.join(RACINE, "etiquetage", "etiquettes.csv")))
            if r["camera"] == cam and r["moment"] == moment}
    par = collections.defaultdict(dict)
    for r in csv.DictReader(open(os.path.join(RACINE, "etiquetage", "previsions.csv"))):
        if r["camera"] == cam and r["moment"] == moment and r["vallee"] in VALLEES[cam]:
            par[(r["date"], r["vallee"])][r["modele"]] = float(r["score"])
    score = {}
    for (d, _), m in par.items():
        tw = sum(poids.get(k, 1.0) for k in m)
        s = sum(poids.get(k, 1.0) * x for k, x in m.items()) / tw if tw else 0
        score[d] = max(score.get(d, 0), s)
    jours = [d for d in etiq if d in score and etiq[d] != "I"]
    n_mer = sum(etiq[d] == "M" for d in jours)
    print(f"{cam} / {moment} : {len(jours)} jours étiquetés, {n_mer} mers de nuages")
    for seuil in (50, 60, 70, 80):
        vues = sum(etiq[d] == "M" and score[d] >= seuil for d in jours)
        fausses = sum(etiq[d] != "M" and score[d] >= seuil for d in jours)
        print(f"  seuil {seuil} : {vues}/{n_mer} détectées, {fausses} fausses alertes")


if __name__ == "__main__":
    main(*sys.argv[1:])
