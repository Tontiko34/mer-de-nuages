#!/usr/bin/env python3
"""
Alerte mer de nuages – Puy-de-Dôme / Monts Dore
================================================

Récupère les prévisions Open-Meteo (AROME/ARPEGE Météo-France, ICON, ECMWF) par
niveaux de pression au-dessus des vallées (Limagne, Val d'Allier, Haute-Dordogne),
reconstruit un profil vertical heure par heure et calcule un score 0-100 de
« mer de nuages visible depuis le sommet » pour chaque puy.

Conditions recherchées :
  1. couche nuageuse (ou brouillard) dense sous le sommet ;
  2. air sec / ciel dégagé à l'altitude du sommet ;
  3. inversion de température (ou atmosphère très stable) qui plafonne la couche ;
  4. vent faible en altitude (le vent casse les inversions).

Aucune dépendance : Python 3.8+ standard uniquement.

Usage :
  python mer_de_nuages.py                       # prévision + tableau de bord + alerte
  python mer_de_nuages.py --sans-alerte         # sans notification
  python mer_de_nuages.py --debut 2026-01-10 --fin 2026-01-20   # rejouer des dates passées
"""

import argparse
import datetime as dt
import html
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import evenements

API_PREVISION = "https://api.open-meteo.com/v1/forecast"
API_HISTORIQUE = "https://historical-forecast-api.open-meteo.com/v1/forecast"
NIVEAUX_HPA = [1000, 975, 950, 925, 900, 850, 800, 750, 700]

JOURS_FR = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]


# --------------------------------------------------------------------------- #
# Récupération des données
# --------------------------------------------------------------------------- #
def variables_horaires():
    v = ["temperature_2m", "relative_humidity_2m", "cloud_cover_low",
         "cloud_cover_mid", "cloud_cover_high", "pressure_msl", "wind_speed_850hPa"]
    for p in NIVEAUX_HPA:
        v += [f"temperature_{p}hPa", f"relative_humidity_{p}hPa",
              f"cloud_cover_{p}hPa", f"geopotential_height_{p}hPa"]
    return v


def telecharger(point, modele, jours, debut=None, fin=None, variables=None, altitude=None):
    params = {
        "latitude": point["lat"],
        "longitude": point["lon"],
        "hourly": ",".join(variables or variables_horaires()),
        "daily": "sunrise,sunset",
        "models": modele,
        "timezone": "Europe/Paris",
        "wind_speed_unit": "kmh",
    }
    if altitude is not None:
        params["elevation"] = altitude   # descente d'échelle à l'altitude du sommet
    if debut:
        params["start_date"], params["end_date"] = debut, fin or debut
        base = API_HISTORIQUE
    else:
        params["forecast_days"] = jours
        base = API_PREVISION
    url = base + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "alerte-mer-de-nuages/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def telecharger_sur(point, modele, cfg, debut, fin, nom, **kw):
    """Téléchargement avec un nouvel essai ; renvoie None (et un avertissement) en cas d'échec."""
    jours = cfg.get("jours_prevision", 3)
    for essai in (1, 2):
        try:
            return telecharger(point, modele, jours, debut, fin, **kw)
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            definitif = isinstance(e, urllib.error.HTTPError) and e.code == 400
            if essai == 2 or definitif:
                detail = ""
                if isinstance(e, urllib.error.HTTPError):
                    try:
                        detail = e.read().decode()[:200]
                    except Exception:
                        pass
                print(f"[avertissement] {modele} / {nom} indisponible : {e} {detail}", file=sys.stderr)
                return None
            time.sleep(5)   # coupure réseau passagère : on réessaie une fois


def recuperer_tout(cfg, debut=None, fin=None):
    """donnees = {"vallees": {modele: {id: rep}}, "couleurs": ..., "etoiles": ..., "neige": ...}"""
    donnees = {"vallees": {}, "couleurs": {}, "etoiles": {}, "neige": {}}
    ev = cfg.get("evenements", {})
    date_ref = dt.date.fromisoformat(debut) if debut else dt.date.today()
    if debut and fin:
        date_ref += (dt.date.fromisoformat(fin) - date_ref) / 2
    for modele in cfg["modeles"]:
        for point in cfg["points_vallee"]:
            rep = telecharger_sur(point, modele, cfg, debut, fin, point["nom"])
            if rep:
                donnees["vallees"].setdefault(modele, {})[point["id"]] = rep

        c = ev.get("couleurs", {})
        if c.get("actif"):
            for lieu in evenements.liste_lieux(c):
                for cle, (lat, lon) in evenements.points_couleurs(c, date_ref, lieu).items():
                    rep = telecharger_sur({"lat": lat, "lon": lon}, modele, cfg, debut, fin,
                                          f"couleurs {lieu['nom']} {cle}", variables=evenements.VARIABLES_COULEURS)
                    if rep:
                        donnees["couleurs"].setdefault(lieu["nom"], {}).setdefault(modele, {})[cle] = rep

        e = ev.get("etoiles", {})
        if e.get("actif"):
            for lieu in evenements.liste_lieux(e):
                rep = telecharger_sur(lieu, modele, cfg, debut, fin, f"étoiles {lieu['nom']}",
                                      variables=evenements.VARIABLES_ETOILES)
                if rep:
                    donnees["etoiles"].setdefault(lieu["nom"], {})[modele] = rep

        n = ev.get("neige", {})
        if n.get("actif"):
            for s in n["sommets"]:
                rep = telecharger_sur(s, modele, cfg, debut, fin, f"neige {s['nom']}",
                                      variables=evenements.VARIABLES_NEIGE, altitude=s["altitude"])
                if rep:
                    donnees["neige"].setdefault(modele, {})[s["nom"]] = rep
    return donnees


# --------------------------------------------------------------------------- #
# Analyse physique d'un profil
# --------------------------------------------------------------------------- #
def borner(x):
    return max(0.0, min(1.0, x))


def valeur(h, cle, i):
    serie = h.get(cle)
    if not serie or i >= len(serie):
        return None
    return serie[i]


def hr_vers_nebulosite(hr):
    """Approximation de Sundqvist (seuil 80 %) si la nébulosité du niveau manque."""
    if hr is None:
        return None
    if hr <= 80:
        return 0.0
    return 100.0 * (1 - math.sqrt(max(0.0, (100 - hr) / 20.0)))


def profil(h, i, altitude_sol):
    """Liste de niveaux {z, t, hr, cc} triés par altitude, au-dessus du sol."""
    niveaux = []
    t2, hr2 = valeur(h, "temperature_2m", i), valeur(h, "relative_humidity_2m", i)
    if t2 is not None:
        # Brouillard au sol : HR ≥ 97 % ≈ couche saturée
        cc_sol = None if hr2 is None else 100 * borner((hr2 - 90) / 7)
        niveaux.append({"z": altitude_sol + 2, "t": t2, "hr": hr2, "cc": cc_sol})
    for p in NIVEAUX_HPA:
        z = valeur(h, f"geopotential_height_{p}hPa", i)
        if z is None or z < altitude_sol + 30:
            continue
        t = valeur(h, f"temperature_{p}hPa", i)
        hr = valeur(h, f"relative_humidity_{p}hPa", i)
        cc = valeur(h, f"cloud_cover_{p}hPa", i)
        if cc is None:
            cc = hr_vers_nebulosite(hr)
        niveaux.append({"z": z, "t": t, "hr": hr, "cc": cc})
    niveaux.sort(key=lambda n: n["z"])
    return niveaux


def interpoler(niveaux, z, cle):
    pts = [(n["z"], n[cle]) for n in niveaux if n[cle] is not None]
    if not pts:
        return None
    if z <= pts[0][0]:
        return pts[0][1]
    for (z1, v1), (z2, v2) in zip(pts, pts[1:]):
        if z1 <= z <= z2:
            return v1 + (v2 - v1) * (z - z1) / (z2 - z1) if z2 > z1 else v1
    return pts[-1][1]


def sommet_couche(niveaux, seuil=50):
    """Altitude estimée du sommet de la couche nuageuse la plus basse."""
    pts = [n for n in niveaux if n["cc"] is not None]
    k = next((j for j, n in enumerate(pts) if n["cc"] >= seuil), None)
    if k is None:
        return None
    j = k
    while j + 1 < len(pts) and pts[j + 1]["cc"] >= seuil:
        j += 1
    if j + 1 == len(pts):
        return pts[j]["z"]
    a, b = pts[j], pts[j + 1]
    frac = (a["cc"] - seuil) / max(1e-6, a["cc"] - b["cc"])
    return a["z"] + frac * (b["z"] - a["z"])


def analyser(niveaux, alt_sommet, vent850):
    """Score 0-100 de mer de nuages visible depuis un sommet d'altitude alt_sommet."""
    if len(niveaux) < 3:
        return None
    dessous = [n["cc"] for n in niveaux if n["z"] <= alt_sommet - 100 and n["cc"] is not None]
    if not dessous:
        return None
    nuages_dessous = max(dessous)

    cc_sommet = interpoler(niveaux, alt_sommet, "cc")
    dessus = [n["cc"] for n in niveaux
              if alt_sommet < n["z"] <= alt_sommet + 400 and n["cc"] is not None]
    nuages_sommet = max([c for c in [cc_sommet] + dessus if c is not None], default=0.0)

    # Stabilité : gradient vertical minimal (°C/km) sous le sommet. < 0 = inversion
    gradients = []
    for a, b in zip(niveaux, niveaux[1:]):
        if a["t"] is None or b["t"] is None or b["z"] - a["z"] < 20:
            continue
        if a["z"] < alt_sommet + 200:
            gradients.append(-(b["t"] - a["t"]) / (b["z"] - a["z"]) * 1000)
    gradient_min = min(gradients) if gradients else None

    sommet = sommet_couche(niveaux)

    s_dessous = borner((nuages_dessous - 40) / 50)        # 40 % → 0 ; 90 % → 1
    s_sommet = borner((60 - nuages_sommet) / 50)          # 10 % → 1 ; 60 % → 0
    s_inversion = 0.5 if gradient_min is None else borner((4 - gradient_min) / 6)
    s_vent = 1.0 if vent850 is None else borner((50 - vent850) / 30)

    score = 100 * s_dessous * s_sommet * (0.4 + 0.6 * s_inversion) * (0.6 + 0.4 * s_vent)
    if sommet is not None and sommet > alt_sommet - 50:
        score *= 0.2   # le sommet est dans le nuage
    return {
        "score": round(score, 1),
        "sommet_nuages": None if sommet is None else round(sommet),
        "nuages_dessous": round(nuages_dessous),
        "nuages_sommet": round(nuages_sommet),
        "gradient_min": None if gradient_min is None else round(gradient_min, 1),
        "vent850": vent850,
    }


# --------------------------------------------------------------------------- #
# Agrégation multi-modèles / multi-vallées
# --------------------------------------------------------------------------- #
def calculer(cfg, donnees):
    if not donnees:
        raise SystemExit("Aucune donnée météo récupérée.")
    # Axe temporel de référence et éphémérides
    ref = next(iter(next(iter(donnees.values())).values()))
    heures = ref["hourly"]["time"]
    soleil = {}
    for d, lever, coucher in zip(ref["daily"]["time"], ref["daily"]["sunrise"], ref["daily"]["sunset"]):
        soleil[d] = (lever, coucher)

    # Résultat brut par (modèle, vallée, sommet, heure)
    brut = {}
    for modele, par_vallee in donnees.items():
        for vid, rep in par_vallee.items():
            h = rep["hourly"]
            index = {t: i for i, t in enumerate(h["time"])}
            alt_sol = rep.get("elevation", 0) or 0
            for t in heures:
                i = index.get(t)
                if i is None:
                    continue
                niv = profil(h, i, alt_sol)
                vent = valeur(h, "wind_speed_850hPa", i)
                for s in cfg["sommets"]:
                    if vid in s["vallees"]:
                        r = analyser(niv, s["altitude"], vent)
                        if r:
                            brut.setdefault((s["nom"], t, vid), []).append(r)

    resultats = {}   # resultats[sommet][heure] = dict
    for s in cfg["sommets"]:
        serie = {}
        for t in heures:
            meilleur = None
            for vid in s["vallees"]:
                rs = brut.get((s["nom"], t, vid))
                if not rs:
                    continue
                scores = [r["score"] for r in rs]
                sommets = [r["sommet_nuages"] for r in rs if r["sommet_nuages"] is not None]
                cand = {
                    "score": round(statistics.median(scores)),
                    "accord": sum(sc >= 50 for sc in scores) / len(scores),
                    "n_modeles": len(scores),
                    "sommet_nuages": round(statistics.median(sommets)) if sommets else None,
                    "nuages_dessous": round(statistics.median(r["nuages_dessous"] for r in rs)),
                    "nuages_sommet": round(statistics.median(r["nuages_sommet"] for r in rs)),
                    "vallee": vid,
                }
                if meilleur is None or cand["score"] > meilleur["score"]:
                    meilleur = cand
            serie[t] = meilleur
        resultats[s["nom"]] = serie

    # Sommet de la couche par vallée (pour le graphique)
    couches = {}
    for p in cfg["points_vallee"]:
        serie = {}
        for t in heures:
            tops = []
            for modele, par_vallee in donnees.items():
                rep = par_vallee.get(p["id"])
                if not rep:
                    continue
                h = rep["hourly"]
                try:
                    i = h["time"].index(t)
                except ValueError:
                    continue
                st = sommet_couche(profil(h, i, rep.get("elevation", 0) or 0))
                if st is not None:
                    tops.append(st)
            med = statistics.median(tops) if tops else None
            serie[t] = round(med) if med is not None and med <= 2300 else None
        couches[p["id"]] = serie
    return heures, soleil, resultats, couches


def heure_de(t):
    return dt.datetime.fromisoformat(t)


def synthese_journaliere(cfg, heures, soleil, resultats):
    """Pour chaque jour et sommet : meilleur créneau diurne (moyenne glissante 3 h) et score au lever."""
    fen = cfg.get("fenetre_matin_heures", 3)
    jours = {}
    for d, (lever, coucher) in soleil.items():
        lv, cc = heure_de(lever), heure_de(coucher)
        debut_jour = lv - dt.timedelta(hours=1)
        par_sommet = {}
        for s in cfg["sommets"]:
            serie = resultats[s["nom"]]
            diurne = [t for t in heures if t.startswith(d) and debut_jour <= heure_de(t) <= cc]
            vals = [(t, serie.get(t)) for t in diurne]
            vals = [(t, r) for t, r in vals if r]
            if not vals:
                continue
            meilleur_t, meilleur_v = None, -1
            for k in range(len(vals)):
                fenetre = [r["score"] for _, r in vals[k:k + 3]]
                m = sum(fenetre) / len(fenetre)
                if m > meilleur_v:
                    meilleur_t, meilleur_v = vals[k][0], m
            matin = [r["score"] for t, r in vals
                     if lv - dt.timedelta(hours=1) <= heure_de(t) <= lv + dt.timedelta(hours=fen)]
            r_best = serie[meilleur_t]
            par_sommet[s["nom"]] = {
                "meilleur": round(meilleur_v),
                "heure": meilleur_t[11:16] if meilleur_v >= 1 else "–",
                "matin": round(sum(matin) / len(matin)) if matin else None,
                "sommet_nuages": r_best["sommet_nuages"],
                "accord": r_best["accord"],
                "n_modeles": r_best["n_modeles"],
                "vallee": r_best["vallee"],
            }
        jours[d] = {"lever": lever[11:16], "coucher": coucher[11:16], "sommets": par_sommet}
    return jours


# --------------------------------------------------------------------------- #
# Notification ntfy
# --------------------------------------------------------------------------- #
def libelle_jour(d, aujourdhui):
    date = dt.date.fromisoformat(d)
    ecart = (date - aujourdhui).days
    nom = {0: "aujourd'hui", 1: "demain", 2: "après-demain"}.get(ecart, "")
    jj = f"{JOURS_FR[date.weekday()].capitalize()} {date.day:02d}/{date.month:02d}"
    return f"{jj} ({nom})" if nom else jj


TITRES = {"mer": "☁️ Mer de nuages", "couleurs": "🌅 Ciel coloré",
          "etoiles": "✨ Ciel étoilé", "neige": "❄️ Neige sur les puys"}


def libelle_nuit(d, aujourdhui):
    date = dt.date.fromisoformat(d)
    lendemain = date + dt.timedelta(days=1)
    jj = f"{JOURS_FR[date.weekday()]} {date.day:02d} → {JOURS_FR[lendemain.weekday()]} {lendemain.day:02d}/{lendemain.month:02d}"
    nom = {0: "cette nuit", 1: "demain soir", 2: "après-demain soir"}.get((date - aujourdhui).days, "")
    return f"Nuit du {jj} ({nom})" if nom else f"Nuit du {jj}"


def candidats_alertes(cfg, jours_mer, couleurs, etoiles, neige, maintenant, tout=False):
    """Liste des événements à venir (dans les ~2,5 jours) qui dépassent leur seuil."""
    ev = cfg.get("evenements", {})
    aujourdhui = maintenant.date()
    limite = aujourdhui + dt.timedelta(days=2)
    cands = []

    seuil = cfg.get("seuil_alerte", 60)
    for d, j in jours_mer.items():
        retenus = [(nom, r) for nom, r in j["sommets"].items() if r["meilleur"] >= seuil]
        if not retenus:
            continue
        nom_best, r_best = max(retenus, key=lambda x: x[1]["meilleur"])
        lignes = []
        for nom, r in retenus:
            top = f", nuages jusqu'à ~{r['sommet_nuages']} m" if r["sommet_nuages"] else ""
            lignes.append(f"• {nom} : {r['meilleur']}/100 vers {r['heure']}{top}, "
                          f"{round(r['accord'] * r['n_modeles'])}/{r['n_modeles']} modèles")
        cands.append({"cle": f"mer:{d}", "type": "mer", "date": d, "valeur": r_best["meilleur"],
                      "quand": dt.datetime.fromisoformat(f"{d}T{r_best['heure']}"),
                      "entete": f"{libelle_jour(d, aujourdhui)} (lever {j['lever']})", "lignes": lignes})

    seuil = ev.get("couleurs", {}).get("seuil", 55)
    groupes = {}
    for lieu, par_jour in couleurs.items():
        for d, par_moment in par_jour.items():
            for moment, r in par_moment.items():
                if r["score"] >= seuil and r["accord"] >= 1:
                    groupes.setdefault((d, moment), []).append((lieu, r))
    for (d, moment), liste in groupes.items():
        best = max(r["score"] for _, r in liste)
        cands.append({
            "cle": f"couleurs:{d}:{moment}", "type": "couleurs", "date": d, "valeur": best,
            "quand": dt.datetime.fromisoformat(f"{d}T{liste[0][1]['heure']}"),
            "entete": f"{libelle_jour(d, aujourdhui)} – {moment} à {liste[0][1]['heure']}",
            "lignes": [f"• {lieu} : {r['score']}/100 (nuages d'altitude {r['toile']} %, "
                       f"horizon côté soleil {100 - r['blocage']} % dégagé), {r['accord']}/{r['n']} modèles"
                       for lieu, r in sorted(liste, key=lambda x: -x[1]["score"])]})

    seuil = ev.get("etoiles", {}).get("seuil", 75)
    groupes = {}
    for lieu, par_nuit in etoiles.items():
        for d, r in par_nuit.items():
            if r["score"] >= seuil and r["accord"] >= min(2, r["n"]):
                groupes.setdefault(d, []).append((lieu, r))
    for d, liste in groupes.items():
        liste.sort(key=lambda x: -x[1]["score"])
        r0 = liste[0][1]
        jour_debut = d if r0["debut"] >= "12:00" else (dt.date.fromisoformat(d) + dt.timedelta(days=1)).isoformat()
        cands.append({
            "cle": f"etoiles:{d}", "type": "etoiles", "date": d, "valeur": r0["score"],
            "quand": dt.datetime.fromisoformat(f"{jour_debut}T{r0['debut']}"),
            "entete": f"{libelle_nuit(d, aujourdhui)} – Lune éclairée à {r0['lune']} %",
            "lignes": [f"• {lieu} : {r['heures']} h de ciel noir et dégagé ({r['debut']}–{r['fin']}), "
                       f"score {r['score']}/100, {r['accord']}/{r['n']} modèles" for lieu, r in liste]})

    seuil = ev.get("neige", {}).get("seuil_cm", 1)
    jours_neige = []
    for d in sorted(neige):
        retenus = [(nom, r) for nom, r in neige[d].items() if r["cm"] >= seuil]
        if retenus and (tout or dt.date.fromisoformat(d) >= aujourdhui):
            jours_neige.append((d, retenus))
    # Jours consécutifs = un seul épisode = une seule alerte
    episodes = []
    for d, retenus in jours_neige:
        if episodes and dt.date.fromisoformat(d) - dt.date.fromisoformat(episodes[-1][-1][0]) == dt.timedelta(days=1):
            episodes[-1].append((d, retenus))
        else:
            episodes.append([(d, retenus)])
    for ep in episodes:
        d0 = ep[0][0]
        lignes = []
        for d, retenus in ep:
            detail = ", ".join(f"{nom} ~{r['cm']:g} cm ({r['min']:g}–{r['max']:g})" for nom, r in retenus)
            lignes.append(f"• {libelle_jour(d, aujourdhui)} : {detail}")
        cands.append({
            "cle": f"neige:{d0}", "type": "neige", "date": d0, "jours": [d for d, _ in ep],
            "valeur": round(sum(max(r["cm"] for _, r in ret) for _, ret in ep), 1),
            "quand": dt.datetime.fromisoformat(f"{ep[-1][0]}T23:59"),
            "entete": "Épisode neigeux" if len(ep) > 1 else "Neige prévue", "lignes": lignes})

    if tout:
        return cands
    return [c for c in cands
            if c["quand"] > maintenant + dt.timedelta(hours=1)
            and dt.date.fromisoformat(c["date"]) <= limite]


def filtrer_nouveaux(cands, etat):
    """Garde les événements jamais signalés, ou qui se sont nettement renforcés."""
    nouveaux = []
    for c in cands:
        if c["type"] == "neige":
            connus = [etat[f"neige:{j}"] for j in c["jours"] if f"neige:{j}" in etat]
            ancien = max(connus) if connus else None
        else:
            ancien = None if f"annule:{c['cle']}" in etat else etat.get(c["cle"])
        if ancien is None:
            nouveaux.append(c)
        elif c["type"] == "neige":
            if c["valeur"] >= 2 * ancien and c["valeur"] - ancien >= 2:
                nouveaux.append(c)
        elif c["valeur"] >= ancien + 15:
            nouveaux.append(c)
    return nouveaux


def valeurs_actuelles(jours_mer, couleurs, etoiles, neige):
    v = {}
    for d, j in jours_mer.items():
        v[f"mer:{d}"] = max((r["meilleur"] for r in j["sommets"].values()), default=0)
    for par_jour in couleurs.values():
        for d, par_moment in par_jour.items():
            for m, r in par_moment.items():
                v[f"couleurs:{d}:{m}"] = max(v.get(f"couleurs:{d}:{m}", 0), r["score"])
    for par_nuit in etoiles.values():
        for d, r in par_nuit.items():
            v[f"etoiles:{d}"] = max(v.get(f"etoiles:{d}", 0), r["score"])
    for d, par_lieu in neige.items():
        v[f"neige:{d}"] = max((r["cm"] for r in par_lieu.values()), default=0)
    return v


def annulations(cfg, etat, actuelles, maintenant):
    """Alertes déjà envoyées dont la prévision s'est nettement dégradée."""
    ev = cfg.get("evenements", {})
    seuils = {"mer": cfg.get("seuil_alerte", 60) - 20,
              "couleurs": ev.get("couleurs", {}).get("seuil", 55) - 20,
              "etoiles": ev.get("etoiles", {}).get("seuil", 75) - 20,
              "neige": ev.get("neige", {}).get("seuil_cm", 1) / 2}
    aujourdhui = maintenant.date().isoformat()
    sortie = []
    for cle, envoye in etat.items():
        if cle.startswith("annule:") or f"annule:{cle}" in etat:
            continue
        morceaux = cle.split(":")
        typ, d = morceaux[0], morceaux[1]
        if d < aujourdhui or (d == aujourdhui and typ != "etoiles" and maintenant.hour >= 10):
            continue
        actuel = actuelles.get(cle)
        if actuel is None or typ not in seuils:
            continue
        if actuel < seuils[typ]:
            sortie.append({"cle": cle, "type": typ, "date": d, "moment": morceaux[2] if len(morceaux) > 2 else "",
                           "avant": envoye, "actuel": actuel})
    return sortie


NOMS_COURTS = {"mer": "Mer de nuages", "couleurs": "Ciel coloré", "etoiles": "Nuit étoilée", "neige": "Neige"}


def construire_message(nouveaux, annules=(), aujourdhui=None):
    if not nouveaux and not annules:
        return None
    types = [t for t in TITRES if any(c["type"] == t for c in nouveaux)]
    blocs = []
    for t in types:
        lignes = [TITRES[t]] if (len(types) > 1 or annules) else []
        precedent = None
        for c in sorted((c for c in nouveaux if c["type"] == t), key=lambda c: c["quand"]):
            if c["entete"] != precedent:
                lignes.append(c["entete"])
                precedent = c["entete"]
            lignes += c["lignes"]
        blocs.append("\n".join(lignes))
    if annules:
        lignes = ["❌ Prévision dégradée (alerte précédente)"]
        for a in sorted(annules, key=lambda a: a["date"]):
            unite = " cm" if a["type"] == "neige" else ""
            quoi = NOMS_COURTS[a["type"]] + (f" ({a['moment']})" if a["moment"] else "")
            jour = libelle_jour(a["date"], aujourdhui) if aujourdhui else a["date"]
            lignes.append(f"• {quoi} – {jour} : {a['avant']:g}{unite} → {a['actuel']:g}{unite}")
        blocs.append("\n".join(lignes))
    fort = any((c["type"] == "mer" and c["valeur"] >= 80) or (c["type"] == "couleurs" and c["valeur"] >= 85)
               or (c["type"] == "etoiles" and c["valeur"] >= 90) for c in nouveaux)
    if not nouveaux:
        titre = "Prévision dégradée"
    elif len(types) == 1 and not annules:
        titre = TITRES[types[0]]
    else:
        titre = "Événements à venir"
    return {"titre": titre, "message": "\n\n".join(blocs), "priorite": 4 if fort else 3}


def lire_etat(chemin):
    try:
        with open(chemin, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def ecrire_etat(chemin, etat, nouveaux, aujourdhui, annules=()):
    for c in nouveaux:
        for cle in ([f"neige:{j}" for j in c["jours"]] if c["type"] == "neige" else [c["cle"]]):
            etat[cle] = max(c["valeur"], 0 if f"annule:{cle}" in etat else etat.get(cle, 0))
            etat.pop(f"annule:{cle}", None)
    for a in annules:
        etat[f"annule:{a['cle']}"] = a["actuel"]
    limite = (aujourdhui - dt.timedelta(days=5)).isoformat()
    etat = {k: v for k, v in etat.items()
            if (re.search(r"\d{4}-\d{2}-\d{2}", k) or [""])[0] >= limite}
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(etat, f, ensure_ascii=False, indent=1, sort_keys=True)


def envoyer_ntfy(cfg, topic, alerte):
    corps = {
        "topic": topic,
        "title": alerte["titre"],
        "message": alerte["message"],
        "tags": ["mountain"],
        "priority": alerte["priorite"],
    }
    if cfg.get("url_tableau_de_bord"):
        corps["click"] = cfg["url_tableau_de_bord"]
    req = urllib.request.Request(
        cfg.get("ntfy_serveur", "https://ntfy.sh").rstrip("/") + "/",
        data=json.dumps(corps).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status


# --------------------------------------------------------------------------- #
# Tableau de bord HTML
# --------------------------------------------------------------------------- #
COULEURS_SOMMETS = ["#7d8792"]
COULEURS_VALLEES = ["#2a78c2", "#d0682b", "#3a9d5d", "#8b5cc4", "#c2417a", "#9a7b4f", "#4f8a8b"]


def couleur_score(s):
    if s is None:
        return "transparent"
    if s >= 75:
        return "#2e9d5b"
    if s >= 50:
        return "#8cc152"
    if s >= 25:
        return "#f0c043"
    return "#d9dde3"


def svg_graphique(heures, soleil, series, y_max, y_label, lignes_h=(), hauteur=230, y_min=0):
    L, H, mg, md, mh, mb = 960, hauteur, 52, 12, 14, 30
    n = len(heures)
    if n < 2:
        return ""
    x = lambda i: mg + (L - mg - md) * i / (n - 1)
    y = lambda v: mh + (H - mh - mb) * (1 - (v - y_min) / (y_max - y_min))
    out = [f'<svg viewBox="0 0 {L} {H}" class="graph" role="img">']
    # nuits
    idx = {t: i for i, t in enumerate(heures)}
    for i, t in enumerate(heures):
        d = t[:10]
        if d in soleil:
            lv, cc = soleil[d]
            if not (lv[:13] <= t[:13] < cc[:13]):
                out.append(f'<rect x="{x(i) - (L - mg - md) / (2 * (n - 1)):.1f}" y="{mh}" '
                           f'width="{(L - mg - md) / (n - 1):.1f}" height="{H - mh - mb}" class="nuit"/>')
    # grille
    pas = 500 if y_max > 200 else 25
    v = y_min
    while v <= y_max:
        out.append(f'<line x1="{mg}" x2="{L - md}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grille"/>'
                   f'<text x="{mg - 6}" y="{y(v) + 4:.1f}" class="axe" text-anchor="end">{v}</text>')
        v += pas
    for i, t in enumerate(heures):
        if t.endswith("00:00"):
            date = dt.date.fromisoformat(t[:10])
            out.append(f'<line x1="{x(i):.1f}" x2="{x(i):.1f}" y1="{mh}" y2="{H - mb}" class="jour"/>'
                       f'<text x="{x(i) + 4:.1f}" y="{H - 10}" class="axe">'
                       f'{JOURS_FR[date.weekday()]} {date.day:02d}/{date.month:02d}</text>')
    out.append(f'<text x="12" y="{mh + 4}" class="axe" transform="rotate(-90 12 {mh + 4})" '
               f'text-anchor="end">{html.escape(y_label)}</text>')
    for k, (val, lib, coul) in enumerate(lignes_h):
        out.append(f'<line x1="{mg}" x2="{L - md}" y1="{y(val):.1f}" y2="{y(val):.1f}" '
                   f'stroke="{coul}" stroke-dasharray="5 4" stroke-width="1.4"/>'
                   f'<text x="{(L - md - 4) if k % 2 else (mg + 4)}" y="{y(val) - 4:.1f}" class="axe" '
                   f'text-anchor="{"end" if k % 2 else "start"}" '
                   f'style="fill:{coul}">{html.escape(lib)}</text>')
    for nom, coul, valeurs in series:
        seg, segs = [], []
        for i, t in enumerate(heures):
            v = valeurs.get(t)
            if v is None:
                if seg:
                    segs.append(seg)
                seg = []
            else:
                seg.append(f"{x(i):.1f},{y(min(max(v, y_min), y_max)):.1f}")
        if seg:
            segs.append(seg)
        for sg in segs:
            if len(sg) == 1:
                cx, cy = sg[0].split(",")
                out.append(f'<circle cx="{cx}" cy="{cy}" r="2.5" fill="{coul}"/>')
            else:
                out.append(f'<polyline points="{" ".join(sg)}" fill="none" stroke="{coul}" '
                           f'stroke-width="2" stroke-linejoin="round"/>')
    out.append("</svg>")
    leg = "".join(f'<span><i style="background:{c}"></i>{html.escape(nm)}</span>'
                  for nm, c, _ in series)
    return f'<div class="legende">{leg}</div>' + "".join(out)


def _pastille(v, seuil=None, grande=False):
    if v is None:
        return '<span class="pastille vide">–</span>'
    if seuil is None:
        coul = couleur_score(v)
    else:
        coul = "#2e9d5b" if v >= seuil else ("#8cc152" if v >= seuil - 15 else
                                              ("#f0c043" if v >= seuil - 35 else "#d9dde3"))
    return f'<span class="pastille{" grande" if grande else ""}" style="background:{coul}">{v}</span>'


def _tuile(icone, titre, corps, note="", large=False):
    note = f'<div class="t-note">{note}</div>' if note else ""
    return (f'<div class="tuile{" large" if large else ""}"><div class="t-titre">{icone} {html.escape(titre)}</div>'
            f'<div class="t-corps">{corps}</div>{note}</div>')


def generer_html(cfg, heures, soleil, resultats, couches, jours, modeles_ok, aujourdhui, mode_hist,
                 couleurs=None, etoiles=None, neige=None):
    couleurs, etoiles, neige = couleurs or {}, etoiles or {}, neige or {}
    ev = cfg.get("evenements", {})
    s_mer = cfg.get("seuil_alerte", 60)
    s_coul = ev.get("couleurs", {}).get("seuil", 55)
    s_etoi = ev.get("etoiles", {}).get("seuil", 75)
    s_neige = ev.get("neige", {}).get("seuil_cm", 1)
    noms_vallees = {p["id"]: p["nom"] for p in cfg["points_vallee"]}
    courts = {l["nom"]: l.get("court", l["nom"]) for x in ("couleurs", "etoiles") if ev.get(x)
              for l in evenements.liste_lieux(ev[x])}
    maj = (evenements.maintenant_local() if not mode_hist else dt.datetime.now()).strftime("%d/%m/%Y %H:%M")

    cartes = []
    for d in sorted(jours):
        j = jours[d]
        tuiles = []

        # Mer de nuages
        sommets = sorted(j["sommets"].items(), key=lambda x: -x[1]["meilleur"])
        if sommets:
            nom, r = sommets[0]
            nb = sum(1 for _, x in sommets if x["meilleur"] >= 50)
            if r["meilleur"] >= 25:
                corps = (f'{_pastille(r["meilleur"], grande=True)}<div><b>{html.escape(nom)}</b> vers {r["heure"]}'
                         + (f'<br><small>nuages jusqu\'à ~{r["sommet_nuages"]} m · '
                            f'{round(r["accord"] * r["n_modeles"])}/{r["n_modeles"]} modèles</small>'
                            if r["sommet_nuages"] else "") + "</div>")
                note = f"{nb} lieu(x) sur {len(sommets)} à 50 ou plus" if nb else "aucun lieu à 50 ou plus"
            else:
                corps, note = f'{_pastille(r["meilleur"], grande=True)}<div>Pas de mer de nuages</div>', ""
            tuiles.append(_tuile("☁️", "Mer de nuages", corps, note, large=True))

        # Lever / coucher colorés
        for moment, icone, titre in (("lever", "🌅", "Lever coloré"), ("coucher", "🌇", "Coucher coloré")):
            lignes = []
            heure = j["lever"] if moment == "lever" else j["coucher"]
            for lieu, par_jour in couleurs.items():
                r = par_jour.get(d, {}).get(moment)
                if r:
                    lignes.append(f'<div class="ligne">{_pastille(r["score"], s_coul)} '
                                  f'{html.escape(courts.get(lieu, lieu))}</div>')
            if lignes:
                tuiles.append(_tuile(icone, f"{titre} · {heure}", "".join(lignes)))

        # Nuit étoilée
        if etoiles:
            lignes, lune = [], None
            for lieu, par_nuit in etoiles.items():
                r = par_nuit.get(d)
                if r:
                    lune = r["lune"]
                    creneau = f' <small>{r["debut"]}–{r["fin"]}</small>' if r["debut"] else ""
                    lignes.append(f'<div class="ligne">{_pastille(r["score"], s_etoi)} '
                                  f'{html.escape(courts.get(lieu, lieu))}{creneau}</div>')
            if lignes:
                tuiles.append(_tuile("✨", "Nuit étoilée", "".join(lignes), f"Lune éclairée à {lune} %"))

        # Neige
        if neige:
            r = neige.get(d, {})
            lignes = [f'<div class="ligne"><b style="color:#2a78c2">{v["cm"]:g} cm</b> {html.escape(n)}</div>'
                      if v["cm"] >= s_neige else f'<div class="ligne"><small>{v["cm"]:g} cm {html.escape(n)}</small></div>'
                      for n, v in r.items() if v["cm"] >= 0.3]
            tuiles.append(_tuile("❄️", "Neige", "".join(lignes) or "<div>Pas de neige</div>"))

        # Détail par sommet (repliable)
        lignes_t = []
        for s in cfg["sommets"]:
            r = j["sommets"].get(s["nom"])
            if not r:
                continue
            top = f"{r['sommet_nuages']} m" if r["sommet_nuages"] else "–"
            lignes_t.append(
                f'<tr><td>{html.escape(s["nom"])} <small>{s["altitude"]} m</small></td>'
                f'<td>{_pastille(r["meilleur"])}</td><td>{r["heure"]}</td><td>{top}</td>'
                f'<td class="opt">{round(r["accord"] * r["n_modeles"])}/{r["n_modeles"]}</td>'
                f'<td class="opt"><small>{html.escape(noms_vallees.get(r["vallee"], ""))}</small></td></tr>')
        detail = (f'<details><summary>Mer de nuages : détail par sommet</summary><table><thead><tr>'
                  f'<th>Sommet</th><th>Score</th><th>Heure</th><th>Haut des nuages</th>'
                  f'<th class="opt">Modèles ≥ 50</th><th class="opt">Vallée</th></tr></thead>'
                  f'<tbody>{"".join(lignes_t)}</tbody></table></details>')

        cartes.append(
            f'<section class="carte"><h3>{html.escape(libelle_jour(d, aujourdhui))}'
            f'<small> lever {j["lever"]} · coucher {j["coucher"]}</small></h3>'
            f'<div class="tuiles">{"".join(tuiles)}</div>{detail}</section>')

    series_top = [(noms_vallees[vid], COULEURS_VALLEES[i % len(COULEURS_VALLEES)], serie)
                  for i, (vid, serie) in enumerate(couches.items())]
    # Sommets d'altitudes voisines regroupés sur une seule ligne pointillée
    groupes = []
    for s_ in sorted(cfg["sommets"], key=lambda x: x["altitude"]):
        court = s_["nom"].replace("Col de la ", "").replace("Puy de la ", "").replace("Puy de l'", "")                         .replace("Puy de ", "")
        if groupes and s_["altitude"] - groupes[-1][0] <= 70:
            groupes[-1][1].append(court)
        else:
            groupes.append([s_["altitude"], [court]])
    alts = [(alt, f"{' / '.join(noms)} ({alt} m)", COULEURS_SOMMETS[0]) for alt, noms in groupes]
    g_top = svg_graphique(heures, soleil, series_top, 2500, "altitude (m)", lignes_h=alts, hauteur=300)

    titre_mode = "Rejeu historique" if mode_hist else "Prévision"
    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mer de nuages &amp; ciel – Puy-de-Dôme</title>
<style>
:root {{ --fond:#f6f7f9; --carte:#fff; --tuile:#f1f3f6; --texte:#1d2733; --doux:#6b7785; --ligne:#e3e7ec; --nuit:rgba(30,45,70,.07); }}
@media (prefers-color-scheme: dark) {{ :root {{ --fond:#14181d; --carte:#1d232a; --tuile:#252c34; --texte:#e6ebf0; --doux:#93a0ad; --ligne:#2c343d; --nuit:rgba(255,255,255,.04); }} }}
body {{ margin:0; font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; background:var(--fond); color:var(--texte); }}
main {{ max-width:1000px; margin:auto; padding:18px 14px 40px; }}
h1 {{ font-size:1.4rem; margin:0 0 4px; }} h2 {{ font-size:1.1rem; margin:28px 0 8px; }}
.sous {{ color:var(--doux); margin:0 0 14px; font-size:.88rem; }}
.carte {{ background:var(--carte); border:1px solid var(--ligne); border-radius:12px; padding:12px 14px; margin:12px 0; }}
.carte h3 {{ margin:0 0 10px; font-size:1.05rem; }} .carte h3 small {{ color:var(--doux); font-weight:400; margin-left:8px; font-size:.85rem; }}
.tuiles {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(210px,1fr)); gap:8px; }}
.tuile {{ background:var(--tuile); border-radius:10px; padding:9px 11px; }}
.tuile.large {{ grid-column:1 / -1; }}
.t-titre {{ font-size:.82rem; color:var(--doux); margin-bottom:6px; font-weight:600; }}
.t-corps {{ display:flex; flex-direction:column; gap:4px; }}
.t-corps:has(.grande) {{ flex-direction:row; align-items:center; gap:10px; }}
.t-note {{ font-size:.78rem; color:var(--doux); margin-top:6px; }}
.ligne {{ display:flex; align-items:center; gap:7px; }}
.pastille {{ display:inline-block; min-width:30px; text-align:center; border-radius:6px; padding:1px 6px; color:#13202c; font-weight:600; font-size:.88rem; }}
.pastille.grande {{ min-width:46px; font-size:1.25rem; padding:4px 8px; border-radius:8px; }}
.pastille.vide {{ background:var(--ligne); color:var(--doux); }}
details {{ margin-top:10px; }} summary {{ cursor:pointer; color:var(--doux); font-size:.88rem; }}
table {{ border-collapse:collapse; width:100%; font-size:.88rem; margin-top:6px; }}
th {{ text-align:left; color:var(--doux); font-weight:500; padding:4px 6px; border-bottom:1px solid var(--ligne); }}
td {{ padding:4px 6px; border-bottom:1px solid var(--ligne); }} td small {{ color:var(--doux); }}
.defile {{ overflow-x:auto; }} .graph {{ width:100%; min-width:720px; height:auto; background:var(--carte); border:1px solid var(--ligne); border-radius:12px; }}
.graph .nuit {{ fill:var(--nuit); }} .graph .grille {{ stroke:var(--ligne); }} .graph .jour {{ stroke:var(--doux); stroke-opacity:.35; }}
.graph .axe {{ font-size:11px; fill:var(--doux); }}
.legende {{ display:flex; flex-wrap:wrap; gap:6px 14px; font-size:.82rem; margin:4px 0 6px; color:var(--doux); }}
.legende i {{ display:inline-block; width:14px; height:3px; border-radius:2px; margin-right:6px; vertical-align:middle; }}
.note {{ color:var(--doux); font-size:.84rem; }}
@media (max-width:640px) {{ .opt {{ display:none; }} .tuiles {{ grid-template-columns:1fr 1fr; }} }}
@media (max-width:380px) {{ .tuiles {{ grid-template-columns:1fr; }} }}
</style></head><body><main>
<h1>Mer de nuages &amp; ciel – Puys &amp; Monts Dore</h1>
<p class="sous">{titre_mode} · mise à jour {maj} · modèles : {html.escape(", ".join(modeles_ok))}</p>
<p class="note">Scores sur 100 · vert foncé = seuil d'alerte atteint (mer de nuages {s_mer}, ciel coloré {s_coul}, nuit étoilée {s_etoi}, neige {s_neige:g} cm).</p>
{"".join(cartes)}
<h2>Altitude du haut des nuages</h2>
<p class="note">Une ligne par vallée : altitude du haut de la couche nuageuse basse (médiane des modèles). Pointillés : altitude des sommets.
Mer de nuages visible quand une ligne passe sous un pointillé ; plus elle reste longtemps basse et stable, plus la mer de nuages dure. Bandes grises : nuit.</p>
<div class="defile">{g_top}</div>
<p class="note">Données : Open-Meteo (Météo-France AROME/ARPEGE, DWD ICON, ECMWF IFS). Altitude des nuages indicative (± 200 m).</p>
</main></body></html>"""


def main():
    ici = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Alerte mer de nuages – Puy-de-Dôme")
    ap.add_argument("--config", default=os.path.join(ici, "config.json"))
    ap.add_argument("--sortie", default=os.path.join(ici, "docs", "index.html"),
                    help="fichier HTML du tableau de bord")
    ap.add_argument("--topic", default=os.environ.get("NTFY_TOPIC"),
                    help="topic ntfy (sinon variable d'environnement NTFY_TOPIC)")
    ap.add_argument("--sans-alerte", action="store_true", help="ne pas envoyer de notification")
    ap.add_argument("--debut", help="rejouer une période passée : date de début AAAA-MM-JJ")
    ap.add_argument("--fin", help="date de fin AAAA-MM-JJ (avec --debut)")
    ap.add_argument("--donnees", help="(test) lire les données depuis un fichier JSON au lieu d'Open-Meteo")
    ap.add_argument("--sauver-donnees", help="enregistrer les données brutes téléchargées (JSON)")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)

    if args.donnees:
        with open(args.donnees, encoding="utf-8") as f:
            donnees = json.load(f)
    else:
        donnees = recuperer_tout(cfg, args.debut, args.fin)
    if args.sauver_donnees:
        with open(args.sauver_donnees, "w", encoding="utf-8") as f:
            json.dump(donnees, f)
    if "vallees" not in donnees:          # ancien format de sauvegarde
        donnees = {"vallees": donnees}

    heures, soleil, resultats, couches = calculer(cfg, donnees["vallees"])
    jours = synthese_journaliere(cfg, heures, soleil, resultats)
    ev = cfg.get("evenements", {})
    couleurs = (evenements.calculer_couleurs(ev["couleurs"], donnees["couleurs"], soleil)
                if donnees.get("couleurs") and ev.get("couleurs", {}).get("actif") else {})
    etoiles = (evenements.calculer_etoiles(ev["etoiles"], donnees["etoiles"], heures)
               if donnees.get("etoiles") and ev.get("etoiles", {}).get("actif") else {})
    neige = (evenements.calculer_neige(ev["neige"], donnees["neige"])
             if donnees.get("neige") and ev.get("neige", {}).get("actif") else {})

    rejeu = bool(args.debut or args.donnees)
    aujourdhui = dt.date.fromisoformat(heures[0][:10]) if rejeu else dt.date.today()

    # Résumé console
    for d in sorted(jours):
        print(f"\n{libelle_jour(d, aujourdhui)}  (lever {jours[d]['lever']})")
        for nom, r in jours[d]["sommets"].items():
            top = f"{r['sommet_nuages']} m" if r["sommet_nuages"] else "-"
            print(f"  {nom:<16} score {r['meilleur']:>3}  vers {r['heure']}  lever {r['matin']!s:>4}"
                  f"  haut des nuages {top:>7}  accord {round(r['accord'] * r['n_modeles'])}/{r['n_modeles']}")
        for lieu, par_jour in couleurs.items():
            c = par_jour.get(d, {})
            if c:
                print(f"  Couleurs {lieu[:22]:<22} " + "  ".join(f"{m} {r['score']:>3} ({r['heure']})" for m, r in c.items()))
        for lieu, par_nuit in etoiles.items():
            if d in par_nuit:
                r = par_nuit[d]
                print(f"  Étoiles {lieu[:23]:<23} score {r['score']:>3}  {r['heures']} h  "
                      f"{r['debut'] or '-'}–{r['fin'] or '-'}  Lune {r['lune']} %")
        if d in neige:
            print("  Neige           " + "  ".join(f"{n} {r['cm']:g} cm" for n, r in neige[d].items()))

    os.makedirs(os.path.dirname(os.path.abspath(args.sortie)), exist_ok=True)
    with open(args.sortie, "w", encoding="utf-8") as f:
        f.write(generer_html(cfg, heures, soleil, resultats, couches, jours,
                             list(donnees["vallees"].keys()), aujourdhui, bool(args.debut),
                             couleurs, etoiles, neige))
    print(f"\nTableau de bord : {args.sortie}")

    if rejeu:
        # Bilan : combien d'alertes chaque type aurait déclenché sur la période
        tous = candidats_alertes(cfg, jours, couleurs, etoiles, neige,
                                 dt.datetime.combine(aujourdhui, dt.time()), tout=True)
        print("\nAlertes qui auraient été envoyées sur la période :")
        for t, titre in TITRES.items():
            liste = [c for c in tous if c["type"] == t]
            print(f"  {titre} : {len(liste)}  " + " ".join(c["cle"].split(":", 1)[1] for c in liste))
        return
    if args.sans_alerte:
        return

    maintenant = evenements.maintenant_local()
    chemin_etat = os.path.join(ici, "etat_alertes.json")
    etat = lire_etat(chemin_etat)
    nouveaux = filtrer_nouveaux(candidats_alertes(cfg, jours, couleurs, etoiles, neige, maintenant), etat)
    annules = annulations(cfg, etat, valeurs_actuelles(jours, couleurs, etoiles, neige), maintenant)
    alerte = construire_message(nouveaux, annules, maintenant.date())
    if not alerte:
        print("Pas de nouvelle alerte.")
        return
    print("\n" + alerte["titre"] + "\n" + alerte["message"])
    if not args.topic:
        print("(NTFY_TOPIC non défini : notification non envoyée)")
        return
    try:
        envoyer_ntfy(cfg, args.topic, alerte)
        ecrire_etat(chemin_etat, etat, nouveaux, maintenant.date(), annules)
        print("Notification envoyée.")
    except Exception as e:
        print(f"[erreur] envoi ntfy : {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
