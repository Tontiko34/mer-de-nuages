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
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request

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


def telecharger(point, modele, jours, debut=None, fin=None):
    params = {
        "latitude": point["lat"],
        "longitude": point["lon"],
        "hourly": ",".join(variables_horaires()),
        "daily": "sunrise,sunset",
        "models": modele,
        "timezone": "Europe/Paris",
        "wind_speed_unit": "kmh",
    }
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


def recuperer_tout(cfg, debut=None, fin=None):
    """donnees[modele][id_vallee] = réponse JSON Open-Meteo"""
    donnees = {}
    for modele in cfg["modeles"]:
        for point in cfg["points_vallee"]:
            try:
                rep = telecharger(point, modele, cfg.get("jours_prevision", 3), debut, fin)
                donnees.setdefault(modele, {})[point["id"]] = rep
            except (urllib.error.URLError, TimeoutError, ValueError) as e:
                detail = ""
                if isinstance(e, urllib.error.HTTPError):
                    try:
                        detail = e.read().decode()[:200]
                    except Exception:
                        pass
                print(f"[avertissement] {modele} / {point['nom']} indisponible : {e} {detail}",
                      file=sys.stderr)
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
            serie[t] = round(statistics.median(tops)) if tops else None
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
    nom = {0: "Aujourd'hui", 1: "Demain", 2: "Après-demain"}.get(ecart, "")
    jj = f"{JOURS_FR[date.weekday()]} {date.day:02d}/{date.month:02d}"
    return f"{nom} ({jj})" if nom else jj


def construire_alerte(cfg, jours, aujourdhui):
    seuil = cfg.get("seuil_alerte", 60)
    cibles = {(aujourdhui + dt.timedelta(days=k)).isoformat() for k in cfg.get("jours_alerte", [1, 2])}
    lignes, maxi = [], 0
    for d in sorted(jours):
        if d not in cibles:
            continue
        retenus = [(nom, r) for nom, r in jours[d]["sommets"].items() if r["meilleur"] >= seuil]
        if not retenus:
            continue
        lignes.append(f"{libelle_jour(d, aujourdhui)} – lever {jours[d]['lever']}")
        for nom, r in retenus:
            maxi = max(maxi, r["meilleur"])
            top = f", nuages jusqu'à ~{r['sommet_nuages']} m" if r["sommet_nuages"] else ""
            matin = f" (lever : {r['matin']})" if r["matin"] is not None else ""
            lignes.append(f"• {nom} : {r['meilleur']}/100 vers {r['heure']}{matin}{top}, "
                          f"{round(r['accord'] * r['n_modeles'])}/{r['n_modeles']} modèles")
    if not lignes:
        return None
    return {"titre": "Mer de nuages en vue", "message": "\n".join(lignes),
            "priorite": 4 if maxi >= 80 else 3}


def envoyer_ntfy(cfg, topic, alerte):
    corps = {
        "topic": topic,
        "title": alerte["titre"],
        "message": alerte["message"],
        "tags": ["cloud", "mountain"],
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
COULEURS_SOMMETS = ["#2a78c2", "#d0682b", "#3a9d5d", "#8b5cc4"]
COULEURS_VALLEES = ["#5a6b7d", "#9a7b4f", "#4f8a8b"]


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
                   f'<text x="{L - md - 4 - k * 115}" y="{y(val) - 4:.1f}" class="axe" text-anchor="end" '
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


def generer_html(cfg, heures, soleil, resultats, couches, jours, modeles_ok, aujourdhui, mode_hist):
    noms_vallees = {p["id"]: p["nom"] for p in cfg["points_vallee"]}
    maj = dt.datetime.now().strftime("%d/%m/%Y %H:%M")

    cartes = []
    for d in sorted(jours):
        j = jours[d]
        lignes = []
        for i, s in enumerate(cfg["sommets"]):
            r = j["sommets"].get(s["nom"])
            if not r:
                continue
            top = f"{r['sommet_nuages']} m" if r["sommet_nuages"] else "–"
            matin = "–" if r["matin"] is None else r["matin"]
            lignes.append(
                f'<tr><td>{html.escape(s["nom"])} <small>{s["altitude"]} m</small></td>'
                f'<td><b class="pastille" style="background:{couleur_score(r["meilleur"])}">'
                f'{r["meilleur"]}</b></td><td>{r["heure"]}</td>'
                f'<td><span class="pastille petite" style="background:{couleur_score(r["matin"])}">'
                f'{matin}</span></td><td>{top}</td>'
                f'<td class="opt">{round(r["accord"] * r["n_modeles"])}/{r["n_modeles"]}</td>'
                f'<td class="opt"><small>{html.escape(noms_vallees.get(r["vallee"], ""))}</small></td></tr>')
        cartes.append(
            f'<section class="carte"><h3>{html.escape(libelle_jour(d, aujourdhui))}'
            f'<small> lever {j["lever"]} · coucher {j["coucher"]}</small></h3>'
            f'<table><thead><tr><th>Sommet</th><th>Score</th><th>Créneau</th><th>Lever</th>'
            f'<th>Haut des nuages</th><th class="opt">Modèles ≥ 50</th><th class="opt">Vallée</th></tr></thead>'
            f'<tbody>{"".join(lignes)}</tbody></table></section>')

    series_score = [(s["nom"], COULEURS_SOMMETS[i % 4],
                     {t: (r["score"] if r else None) for t, r in resultats[s["nom"]].items()})
                    for i, s in enumerate(cfg["sommets"])]
    g_score = svg_graphique(heures, soleil, series_score, 100, "score", hauteur=210)

    series_top = [(noms_vallees[vid], COULEURS_VALLEES[i % 3], serie)
                  for i, (vid, serie) in enumerate(couches.items())]
    alts = [(s["altitude"], s["nom"], COULEURS_SOMMETS[i % 4]) for i, s in enumerate(cfg["sommets"])]
    g_top = svg_graphique(heures, soleil, series_top, 2500, "altitude (m)", lignes_h=alts)

    titre_mode = "Rejeu historique" if mode_hist else "Prévision"
    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mer de nuages – Puy-de-Dôme</title>
<style>
:root {{ --fond:#f6f7f9; --carte:#fff; --texte:#1d2733; --doux:#6b7785; --ligne:#e3e7ec; --nuit:rgba(30,45,70,.07); }}
@media (prefers-color-scheme: dark) {{ :root {{ --fond:#14181d; --carte:#1d232a; --texte:#e6ebf0; --doux:#93a0ad; --ligne:#2c343d; --nuit:rgba(255,255,255,.04); }} }}
body {{ margin:0; font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; background:var(--fond); color:var(--texte); }}
main {{ max-width:1000px; margin:auto; padding:20px 16px 40px; }}
h1 {{ font-size:1.5rem; margin:0 0 4px; }} h2 {{ font-size:1.1rem; margin:28px 0 8px; }}
.sous {{ color:var(--doux); margin:0 0 18px; font-size:.9rem; }}
.carte {{ background:var(--carte); border:1px solid var(--ligne); border-radius:10px; padding:12px 14px; margin:12px 0; overflow-x:auto; }}
.carte h3 {{ margin:0 0 8px; font-size:1rem; }} .carte h3 small {{ color:var(--doux); font-weight:400; margin-left:8px; }}
table {{ border-collapse:collapse; width:100%; font-size:.9rem; }}
th {{ text-align:left; color:var(--doux); font-weight:500; padding:4px 8px; border-bottom:1px solid var(--ligne); white-space:nowrap; }}
td {{ padding:5px 8px; border-bottom:1px solid var(--ligne); white-space:nowrap; }} td small {{ color:var(--doux); }}
.pastille {{ display:inline-block; min-width:34px; text-align:center; border-radius:6px; padding:1px 6px; color:#13202c; }}
.petite {{ min-width:26px; font-size:.85rem; }}
.graph {{ width:100%; height:auto; background:var(--carte); border:1px solid var(--ligne); border-radius:10px; }}
.graph .nuit {{ fill:var(--nuit); }} .graph .grille {{ stroke:var(--ligne); }} .graph .jour {{ stroke:var(--doux); stroke-opacity:.35; }}
.graph .axe {{ font-size:11px; fill:var(--doux); }}
.legende {{ display:flex; flex-wrap:wrap; gap:14px; font-size:.85rem; margin:4px 0 6px; color:var(--doux); }}
.legende i {{ display:inline-block; width:14px; height:3px; border-radius:2px; margin-right:6px; vertical-align:middle; }}
.note {{ color:var(--doux); font-size:.85rem; }}
@media (max-width:640px) {{ .opt {{ display:none; }} th, td {{ padding:4px 5px; }} }}
</style></head><body><main>
<h1>Mer de nuages – Chaîne des Puys &amp; Monts Dore</h1>
<p class="sous">{titre_mode} · mise à jour {maj} · modèles : {html.escape(", ".join(modeles_ok))}</p>
<p class="note">Score 0–100 : nuages denses sous le sommet × ciel dégagé au sommet × inversion × vent faible.
<b style="color:#2e9d5b">≥ 75</b> très probable · <b style="color:#8cc152">50–75</b> probable · <b style="color:#c9a227">25–50</b> possible.
« Lever » = score moyen autour du lever du soleil.</p>
{"".join(cartes)}
<h2>Score heure par heure</h2>{g_score}
<h2>Altitude estimée du sommet de la couche nuageuse</h2>
<p class="note">Lignes pleines : haut de la couche nuageuse la plus basse au-dessus de chaque vallée (médiane des modèles). Pointillés : altitude des sommets. La mer de nuages est visible quand la ligne pleine passe sous un pointillé et que le score est élevé. Bandes grises : nuit.</p>
{g_top}
<p class="note">Données : Open-Meteo (Météo-France AROME/ARPEGE, DWD ICON, ECMWF IFS). Résolution verticale limitée aux niveaux de pression (≈ 250–500 m d'écart) : l'altitude du haut des nuages est indicative (± 200 m).</p>
</main></body></html>"""


# --------------------------------------------------------------------------- #
# Programme principal
# --------------------------------------------------------------------------- #
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

    heures, soleil, resultats, couches = calculer(cfg, donnees)
    jours = synthese_journaliere(cfg, heures, soleil, resultats)
    aujourdhui = dt.date.fromisoformat(heures[0][:10]) if (args.debut or args.donnees) else dt.date.today()

    # Résumé console
    for d in sorted(jours):
        print(f"\n{libelle_jour(d, aujourdhui)}  (lever {jours[d]['lever']})")
        for nom, r in jours[d]["sommets"].items():
            top = f"{r['sommet_nuages']} m" if r["sommet_nuages"] else "-"
            print(f"  {nom:<16} score {r['meilleur']:>3}  vers {r['heure']}  lever {r['matin']!s:>4}"
                  f"  haut des nuages {top:>7}  accord {round(r['accord'] * r['n_modeles'])}/{r['n_modeles']}")

    os.makedirs(os.path.dirname(os.path.abspath(args.sortie)), exist_ok=True)
    with open(args.sortie, "w", encoding="utf-8") as f:
        f.write(generer_html(cfg, heures, soleil, resultats, couches, jours,
                             list(donnees.keys()), aujourdhui, bool(args.debut)))
    print(f"\nTableau de bord : {args.sortie}")

    if args.debut or args.sans_alerte:
        return
    alerte = construire_alerte(cfg, jours, aujourdhui)
    if not alerte:
        print("Pas d'alerte : aucun créneau au-dessus du seuil.")
        return
    print("\n" + alerte["message"])
    if not args.topic:
        print("(NTFY_TOPIC non défini : notification non envoyée)")
        return
    try:
        envoyer_ntfy(cfg, args.topic, alerte)
        print("Notification envoyée.")
    except Exception as e:
        print(f"[erreur] envoi ntfy : {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
