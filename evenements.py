"""
Autres événements surveillés en plus de la mer de nuages :
  * couleurs du ciel au lever / coucher du soleil ;
  * ciel étoilé (nuit noire, sans Lune, sans nuages, air sec) ;
  * neige sur les puys.

Astronomie : formules simplifiées (précision ~1°, largement suffisante ici),
sans dépendance externe.
"""

import datetime as dt
import math
import statistics

RAD = math.pi / 180
OBLIQUITE = RAD * 23.4397
J2000 = dt.datetime(2000, 1, 1, 12, tzinfo=dt.timezone.utc)


# --------------------------------------------------------------------------- #
# Heure locale (Europe/Paris) <-> UTC, sans base de fuseaux horaires
# --------------------------------------------------------------------------- #
def _dernier_dimanche(an, mois):
    d = (dt.date(an, mois + 1, 1) - dt.timedelta(days=1)) if mois < 12 else dt.date(an, 12, 31)
    return d - dt.timedelta(days=(d.weekday() + 1) % 7)


def local_vers_utc(t_local):
    t = dt.datetime.fromisoformat(t_local) if isinstance(t_local, str) else t_local
    debut = dt.datetime.combine(_dernier_dimanche(t.year, 3), dt.time(2))
    fin = dt.datetime.combine(_dernier_dimanche(t.year, 10), dt.time(3))
    decalage = 2 if debut <= t < fin else 1
    return (t - dt.timedelta(hours=decalage)).replace(tzinfo=dt.timezone.utc)


def maintenant_local():
    u = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    debut = dt.datetime.combine(_dernier_dimanche(u.year, 3), dt.time(1))
    fin = dt.datetime.combine(_dernier_dimanche(u.year, 10), dt.time(1))
    return u + dt.timedelta(hours=2 if debut <= u < fin else 1)


# --------------------------------------------------------------------------- #
# Soleil et Lune
# --------------------------------------------------------------------------- #
def _jours(t_utc):
    return (t_utc - J2000).total_seconds() / 86400


def _equatoriales(l, b):
    ra = math.atan2(math.sin(l) * math.cos(OBLIQUITE) - math.tan(b) * math.sin(OBLIQUITE), math.cos(l))
    dec = math.asin(math.sin(b) * math.cos(OBLIQUITE) + math.cos(b) * math.sin(OBLIQUITE) * math.sin(l))
    return ra, dec


def _soleil(d):
    m = RAD * (357.5291 + 0.98560028 * d)
    c = RAD * (1.9148 * math.sin(m) + 0.02 * math.sin(2 * m) + 0.0003 * math.sin(3 * m))
    return _equatoriales(m + c + RAD * 102.9372 + math.pi, 0)


def _lune(d):
    l0 = RAD * (218.316 + 13.176396 * d)
    m = RAD * (134.963 + 13.064993 * d)
    f = RAD * (93.272 + 13.229350 * d)
    return _equatoriales(l0 + RAD * 6.289 * math.sin(m), RAD * 5.128 * math.sin(f))


def hauteur_azimut(t_utc, lat, lon, astre="soleil"):
    """Hauteur (°) et azimut (° depuis le nord, sens horaire)."""
    d = _jours(t_utc)
    ra, dec = _soleil(d) if astre == "soleil" else _lune(d)
    phi = RAD * lat
    h_angle = RAD * (280.16 + 360.9856235 * d) + RAD * lon - ra
    h = math.asin(math.sin(phi) * math.sin(dec) + math.cos(phi) * math.cos(dec) * math.cos(h_angle))
    az = math.atan2(math.sin(h_angle), math.cos(h_angle) * math.sin(phi) - math.tan(dec) * math.cos(phi))
    return h / RAD, (az / RAD + 180) % 360


def illumination_lune(t_utc):
    d = _jours(t_utc)
    ras, decs = _soleil(d)
    ram, decm = _lune(d)
    c = math.sin(decs) * math.sin(decm) + math.cos(decs) * math.cos(decm) * math.cos(ras - ram)
    return (1 - max(-1.0, min(1.0, c))) / 2


def azimuts_lever_coucher(date, lat):
    """Azimut approché du lever et du coucher du soleil pour une date."""
    t = dt.datetime.combine(date, dt.time(12), tzinfo=dt.timezone.utc)
    _, dec = _soleil(_jours(t))
    a = math.acos(max(-1.0, min(1.0, math.sin(dec) / math.cos(RAD * lat)))) / RAD
    return a, 360 - a


def point_destination(lat, lon, azimut, distance_km):
    r = distance_km / 6371.0
    th, p1, l1 = RAD * azimut, RAD * lat, RAD * lon
    p2 = math.asin(math.sin(p1) * math.cos(r) + math.cos(p1) * math.sin(r) * math.cos(th))
    l2 = l1 + math.atan2(math.sin(th) * math.sin(r) * math.cos(p1), math.cos(r) - math.sin(p1) * math.sin(p2))
    return round(p2 / RAD, 3), round(l2 / RAD, 3)


# --------------------------------------------------------------------------- #
# Outils
# --------------------------------------------------------------------------- #
def borner(x):
    return max(0.0, min(1.0, x))


def _val(rep, cle, t):
    h = rep["hourly"]
    idx = rep.setdefault("_index", {s: i for i, s in enumerate(h["time"])})
    i = idx.get(t)
    serie = h.get(cle)
    if i is None or not serie or i >= len(serie):
        return None
    return serie[i]


def _heure_ronde(t_local):
    t = dt.datetime.fromisoformat(t_local)
    if t.minute >= 30:
        t += dt.timedelta(hours=1)
    return t.strftime("%Y-%m-%dT%H:00")


def _mediane(vals):
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else None


# --------------------------------------------------------------------------- #
# 1. Couleurs du ciel au lever / coucher
# --------------------------------------------------------------------------- #
VARIABLES_COULEURS = ["cloud_cover_low", "cloud_cover_mid", "cloud_cover_high", "precipitation"]


def points_couleurs(cfg_c, date_ref):
    obs = cfg_c["lieu"]
    az_lever, az_coucher = azimuts_lever_coucher(date_ref, obs["lat"])
    pts = {"obs": (obs["lat"], obs["lon"])}
    for moment, az in (("lever", az_lever), ("coucher", az_coucher)):
        for dist in cfg_c.get("distances_km", [60, 150]):
            pts[f"{moment}_{dist}"] = point_destination(obs["lat"], obs["lon"], az, dist)
    return pts


def _heures_autour(t, k=1):
    t0 = dt.datetime.fromisoformat(t)
    return [(t0 + dt.timedelta(hours=j)).strftime("%Y-%m-%dT%H:00") for j in range(-k, k + 1)]


def _moyenne_ensemble(donnees_c, cle_point, var, t):
    """Moyenne sur les modèles et sur l'heure ± 1 h : lisse les désaccords de timing et de position."""
    vals = [_val(rep[cle_point], var, h) for rep in donnees_c.values() if cle_point in rep
            for h in _heures_autour(t)]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _score_couleur_valeurs(haut, moyen, bas, pluie, blocage):
    # « Toile » : nuages moyens/hauts au-dessus de soi, qui vont s'embraser
    toile = min(100.0, haut + 0.6 * moyen)
    if toile < 15:
        s_toile = 0.0
    elif toile < 40:
        s_toile = (toile - 15) / 25
    elif toile <= 70:
        s_toile = 1.0
    else:
        s_toile = 1 - 0.6 * (toile - 70) / 30
    s_chemin = (1 - blocage / 100) ** 1.5           # horizon côté soleil dégagé
    s_local = 1 - 0.7 * bas / 100                   # pas de plafond bas au-dessus de soi
    s_pluie = 0.5 if pluie > 0.2 else 1.0
    return 100 * s_toile * s_chemin * s_local * s_pluie, toile


def _blocage(get, cfg_c, moment):
    b = 0.0
    for dist in cfg_c.get("distances_km", [60, 150]):
        bas = get(f"{moment}_{dist}", "cloud_cover_low")
        moy = get(f"{moment}_{dist}", "cloud_cover_mid")
        if bas is None:
            continue
        b = max(b, bas, (moy - 30) if (moy or 0) > 70 else 0)
    return b


def calculer_couleurs(cfg_c, donnees_c, soleil):
    """resultat[date] = {"lever": {...}, "coucher": {...}}"""
    res = {}
    for d, (lever, coucher) in soleil.items():
        res[d] = {}
        for moment, t_ev in (("lever", lever), ("coucher", coucher)):
            t = _heure_ronde(t_ev)
            # Score de consensus (moyenne des modèles)
            g = lambda cle, var: _moyenne_ensemble(donnees_c, cle, var, t)
            haut, moyen = g("obs", "cloud_cover_high"), g("obs", "cloud_cover_mid")
            if haut is None or moyen is None:
                continue
            blocage = _blocage(g, cfg_c, moment)
            score, toile = _score_couleur_valeurs(haut, moyen, g("obs", "cloud_cover_low") or 0,
                                                  g("obs", "precipitation") or 0, blocage)
            # Accord : nombre de modèles qui, seuls, donnent aussi un ciel favorable
            accord, n = 0, 0
            for rep in donnees_c.values():
                if "obs" not in rep:
                    continue
                gm = lambda cle, var: (lambda v: sum(v) / len(v) if v else None)(
                    [x for x in (_val(rep[cle], var, h) for h in _heures_autour(t)) if x is not None]
                    if cle in rep else [])
                hm, mm = gm("obs", "cloud_cover_high"), gm("obs", "cloud_cover_mid")
                if hm is None or mm is None:
                    continue
                n += 1
                sc, _ = _score_couleur_valeurs(hm, mm, gm("obs", "cloud_cover_low") or 0,
                                               gm("obs", "precipitation") or 0, _blocage(gm, cfg_c, moment))
                accord += sc >= 50
            res[d][moment] = {"score": round(score), "accord": accord, "n": n, "heure": t_ev[11:16],
                              "toile": round(toile), "blocage": round(blocage)}
    return res


# --------------------------------------------------------------------------- #
# 2. Ciel étoilé
# --------------------------------------------------------------------------- #
VARIABLES_ETOILES = ["cloud_cover_high", "cloud_cover_mid", "cloud_cover_low",
                     "cloud_cover_850hPa", "cloud_cover_800hPa", "relative_humidity_850hPa"]


def _nebulosite_ciel(rep, t, alt_site):
    """Nuages au-dessus du site (les nuages bas sous le sommet ne gênent pas)."""
    haut = _val(rep, "cloud_cover_high", t)
    moyen = _val(rep, "cloud_cover_mid", t)
    if haut is None or moyen is None:
        return None
    if alt_site >= 1100:
        bas = max(_val(rep, "cloud_cover_850hPa", t) or 0, _val(rep, "cloud_cover_800hPa", t) or 0)
    else:
        bas = _val(rep, "cloud_cover_low", t) or 0
    return max(haut, moyen, bas)


def calculer_etoiles(cfg_e, donnees_e, heures):
    lieu = cfg_e["lieu"]
    alt = lieu.get("altitude", 0)
    max_nuages = cfg_e.get("nebulosite_max", 15)
    h_cible = cfg_e.get("heures_cible", 4)
    modeles = list(donnees_e.values())
    if not modeles:
        return {}

    info = {}
    for t in heures:
        tu = local_vers_utc(t)
        h_sol, _ = hauteur_azimut(tu, lieu["lat"], lieu["lon"], "soleil")
        h_lune, _ = hauteur_azimut(tu, lieu["lat"], lieu["lon"], "lune")
        illum = illumination_lune(tu)
        nuit_noire = h_sol < -18 and (h_lune < -2 or illum < 0.2)
        neb = [_nebulosite_ciel(rep, t, alt) for rep in modeles]
        neb = [n for n in neb if n is not None]
        hr = _mediane([_val(rep, "relative_humidity_850hPa", t) for rep in modeles])
        info[t] = {"noire": nuit_noire, "neb": _mediane(neb),
                   "accord": sum(n <= max_nuages for n in neb), "n": len(neb),
                   "hr": hr, "illum": illum, "lune_levee": h_lune > -2}

    res = {}
    jours = sorted({t[:10] for t in heures})
    for d in jours:
        d1 = (dt.date.fromisoformat(d) + dt.timedelta(days=1)).isoformat()
        nuit = [t for t in heures if (t[:10] == d and t[11:13] >= "17") or (t[:10] == d1 and t[11:13] <= "07")]
        if not nuit or not any(t[:10] == d1 for t in nuit):
            continue   # nuit incomplète en fin de prévision
        noires = [t for t in nuit if info[t]["noire"]]
        meilleure, courante = [], []
        for t in nuit:
            i = info[t]
            ok = i["noire"] and i["neb"] is not None and i["neb"] <= max_nuages
            if ok:
                courante.append(t)
                if len(courante) > len(meilleure):
                    meilleure = list(courante)
            else:
                courante = []
        illum_minuit = illumination_lune(local_vers_utc(f"{d1}T00:00"))
        if meilleure:
            transp = statistics.mean(
                1.0 if (info[t]["hr"] or 0) <= 60 else max(0.4, 1 - 0.6 * ((info[t]["hr"] - 60) / 35))
                for t in meilleure)
            score = 100 * min(1.0, len(meilleure) / h_cible) * transp
            accord = min(info[t]["accord"] for t in meilleure)
            n = min(info[t]["n"] for t in meilleure)
            debut = meilleure[0][11:16]
            fin = (dt.datetime.fromisoformat(meilleure[-1]) + dt.timedelta(hours=1)).strftime("%H:%M")
        else:
            score, accord, n, debut, fin, transp = 0, 0, len(modeles), None, None, None
        res[d] = {"score": round(score), "heures": len(meilleure), "debut": debut, "fin": fin,
                  "heures_noires": len(noires), "accord": accord, "n": n,
                  "lune": round(100 * illum_minuit)}
    return res


# --------------------------------------------------------------------------- #
# 3. Neige sur les puys
# --------------------------------------------------------------------------- #
VARIABLES_NEIGE = ["temperature_2m", "precipitation", "snowfall"]


def calculer_neige(cfg_n, donnees_n):
    """resultat[date][sommet] = {"cm": médiane, "min", "max", "n"}"""
    res = {}
    for s in cfg_n["sommets"]:
        par_modele = {}
        for modele, par_sommet in donnees_n.items():
            rep = par_sommet.get(s["nom"])
            if not rep:
                continue
            h = rep["hourly"]
            cumul = {}
            for i, t in enumerate(h["time"]):
                p = (h.get("precipitation") or [None] * (i + 1))[i]
                temp = (h.get("temperature_2m") or [None] * (i + 1))[i]
                if p is None or temp is None:
                    continue
                # Fraction neigeuse : 100 % sous 0 °C, 0 % au-dessus de 2 °C ; ~1 cm par mm d'eau
                frac = borner((2 - temp) / 2)
                cumul[t[:10]] = cumul.get(t[:10], 0) + p * frac
            par_modele[modele] = cumul
        jours = sorted({d for c in par_modele.values() for d in c})
        for d in jours:
            vals = [c[d] for c in par_modele.values() if d in c]
            res.setdefault(d, {})[s["nom"]] = {
                "cm": round(statistics.median(vals), 1), "min": round(min(vals), 1),
                "max": round(max(vals), 1), "n": len(vals)}
    return res
