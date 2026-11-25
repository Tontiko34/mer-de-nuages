"""
Extrait, pour chaque jour d'une période, ce que prévoyaient les modèles (prévisions archivées
Open-Meteo) au moment des images de webcams : une ligne par jour × moment × caméra × modèle × vallée.

Sortie : etiquetage/previsions.csv (compact, pour l'apprentissage).
Usage  : python outils/features_mer.py AAAA-MM-JJ AAAA-MM-JJ
"""
import csv
import datetime as dt
import json
import os
import sys
import time

RACINE_DEPOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE_DEPOT)
import mer_de_nuages as m  # noqa: E402

CAMERAS = {"pdd": 1465, "montdore": 1780}
SORTIE = os.path.join(RACINE_DEPOT, "etiquetage", "previsions.csv")
CHAMPS = ["date", "moment", "camera", "modele", "vallee", "heure", "score", "sommet_nuages",
          "nuages_dessous", "nuages_sommet", "gradient_min", "vent850", "hr2m", "t2m", "cc_bas"]


def lever(date):
    import evenements as ev
    t = dt.datetime.combine(date, dt.time(4, 0))
    while t.hour < 11:
        if ev.hauteur_azimut(ev.local_vers_utc(t), 45.77, 2.96)[0] > -0.83:
            return t
        t += dt.timedelta(minutes=2)
    return dt.datetime.combine(date, dt.time(8, 0))


def tranches(debut, fin, jours=92):
    d = dt.date.fromisoformat(debut)
    f = dt.date.fromisoformat(fin)
    while d <= f:
        e = min(f, d + dt.timedelta(days=jours - 1))
        yield d.isoformat(), e.isoformat()
        d = e + dt.timedelta(days=1)


def main(debut, fin):
    with open(os.path.join(RACINE_DEPOT, "config.json"), encoding="utf-8") as fc:
        cfg = json.load(fc)
    os.makedirs(os.path.dirname(SORTIE), exist_ok=True)
    nouveau = not os.path.exists(SORTIE)
    f = open(SORTIE, "a", newline="", encoding="utf-8")
    w = csv.writer(f)
    if nouveau:
        w.writerow(CHAMPS)
    for d0, d1 in tranches(debut, fin):
        for modele in cfg["modeles"]:
            for p in cfg["points_vallee"]:
                rep = m.telecharger_sur(p, modele, cfg, d0, d1, p["nom"])
                time.sleep(1)
                if not rep:
                    continue
                h = rep["hourly"]
                index = {t: i for i, t in enumerate(h["time"])}
                alt_sol = rep.get("elevation", 0) or 0
                d = dt.date.fromisoformat(d0)
                while d <= dt.date.fromisoformat(d1):
                    lv = lever(d)
                    for moment, decal in (("lever", 50), ("matinee", 150)):
                        t = lv + dt.timedelta(minutes=decal)
                        if t.minute >= 30:
                            t += dt.timedelta(hours=1)
                        cle = t.strftime("%Y-%m-%dT%H:00")
                        i = index.get(cle)
                        if i is None:
                            continue
                        niv = m.profil(h, i, alt_sol)
                        vent = m.valeur(h, "wind_speed_850hPa", i)
                        for cam, alt in CAMERAS.items():
                            r = m.analyser(niv, alt, vent)
                            if not r:
                                continue
                            w.writerow([d.isoformat(), moment, cam, modele, p["id"], cle[11:16], r["score"],
                                        r["sommet_nuages"], r["nuages_dessous"], r["nuages_sommet"],
                                        r["gradient_min"], vent, m.valeur(h, "relative_humidity_2m", i),
                                        m.valeur(h, "temperature_2m", i), m.valeur(h, "cloud_cover_low", i)])
                    d += dt.timedelta(days=1)
                f.flush()
        print(f"tranche {d0} → {d1} faite")
    f.close()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
