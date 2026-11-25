"""
Collecte des images de webcams pour l'apprentissage (vérité terrain).

Pour chaque jour de la période et chaque caméra, récupère 2 images du matin :
  * « lever »   : ~50 min après le lever du soleil (moment typique de la mer de nuages) ;
  * « matinee » : ~2 h 30 après le lever (persistance).
Enregistre une vignette et quelques mesures d'image dans etiquetage/.

Caméras :
  * pdd      : sommet du Puy de Dôme (OPGC, 1465 m) – archives depuis le 21/12/2012, heure locale ;
  * montdore : sommet de la station du Mont-Dore (1780 m) – archives depuis 10/2025, heure UTC.

Usage : python outils/collecte_webcams.py AAAA-MM-JJ AAAA-MM-JJ [pdd,montdore]
Téléchargement lent (≈1 requête/s) pour ne pas charger les serveurs.
"""
import csv
import datetime as dt
import io
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import evenements as ev  # noqa: E402
from PIL import Image, ImageStat  # noqa: E402

UA = {"User-Agent": "alerte-mer-de-nuages (usage personnel, accord OPGC)"}
RACINE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "etiquetage")
CSV = os.path.join(RACINE, "images.csv")
CHAMPS = ["date", "camera", "moment", "heure_locale", "fichier", "lum", "contraste", "satur",
          "lum_haut", "lum_bas", "contraste_bas", "satur_haut", "bleu_haut"]
LAT, LON = 45.77, 2.96


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read()


def lever(date):
    """Heure locale du lever du soleil (à 2 min près)."""
    t = dt.datetime.combine(date, dt.time(4, 0))
    while t.hour < 11:
        h, _ = ev.hauteur_azimut(ev.local_vers_utc(t), LAT, LON)
        if h > -0.83:
            return t
        t += dt.timedelta(minutes=2)
    return dt.datetime.combine(date, dt.time(8, 0))


def mesures(img, cam):
    im = img.convert("RGB")
    w, h = im.size
    hsv = im.convert("HSV")

    def stat(box):
        r = im.crop(box)
        s = ImageStat.Stat(r.convert("L"))
        sh = ImageStat.Stat(hsv.crop(box))
        rgb = ImageStat.Stat(r).mean
        return s.mean[0], s.stddev[0], sh.mean[1], rgb[2] - (rgb[0] + rgb[1]) / 2

    haut = (0, int(0.05 * h), w, int(0.30 * h))
    if cam == "pdd":
        bas = (int(0.55 * w), int(0.30 * h), w, int(0.55 * h))
    else:
        bas = (0, int(0.25 * h), int(0.6 * w), int(0.55 * h))
    lg, cg, sg, _ = stat((0, 0, w, h))
    lh, _, sh, bh = stat(haut)
    lb, cb, _, _ = stat(bas)
    return [round(x, 1) for x in (lg, cg, sg, lh, lb, cb, sh, bh)]


def deja_fait():
    if not os.path.exists(CSV):
        return set()
    with open(CSV, encoding="utf-8") as f:
        return {(r["date"], r["camera"], r["moment"]) for r in csv.DictReader(f) if r["fichier"]}


def image_pdd(t_local):
    t = t_local.replace(minute=(t_local.minute // 10) * 10 + 2, second=0, microsecond=0)
    url = (f"https://wwwobs.univ-bpclermont.fr/opgc/Archive-webcam/pdd/{t:%Y/%m/%d}/"
           f"webcampdd-{t:%Y%m%d%H%M}.jpg")
    return t, get(url)


_cache_md = {}


def image_montdore(t_local):
    base = "https://skol-webcams-archives.s3.fr-par.scw.cloud/"
    tu = ev.local_vers_utc(t_local).replace(tzinfo=None)
    jour = f"{tu:%Y/%m/%d}"
    if jour not in _cache_md:
        txt = get(f"{base}?list-type=2&prefix=archives/sancy_sommet-station/small/{jour}/").decode()
        _cache_md[jour] = re.findall(r"<Key>([^<]+)</Key>", txt)
        time.sleep(0.5)
    meilleur, ecart = None, None
    for k in _cache_md[jour]:
        m = re.search(r"-(\d{8}T\d{4})", k)
        tk = dt.datetime.strptime(m.group(1), "%Y%m%dT%H%M")
        e = abs((tk - tu).total_seconds())
        if ecart is None or e < ecart:
            meilleur, ecart = (k, tk), e
    if not meilleur or ecart > 45 * 60:
        raise LookupError("pas d'image à moins de 45 min")
    k, tk = meilleur
    return t_local + (tk - tu), get(base + k)


CAMERAS = {"pdd": image_pdd, "montdore": image_montdore}


def main(debut, fin, cams=("pdd", "montdore")):
    os.makedirs(RACINE, exist_ok=True)
    fait = deja_fait()
    nouveau = not os.path.exists(CSV)
    f = open(CSV, "a", newline="", encoding="utf-8")
    w = csv.writer(f)
    if nouveau:
        w.writerow(CHAMPS)
    d = dt.date.fromisoformat(debut)
    n = 0
    while d <= dt.date.fromisoformat(fin):
        lv = lever(d)
        for moment, decal in (("lever", 50), ("matinee", 150)):
            t = lv + dt.timedelta(minutes=decal)
            for cam in cams:
                if (d.isoformat(), cam, moment) in fait:
                    continue
                if cam == "montdore" and d < dt.date(2025, 10, 1):
                    continue
                try:
                    t_reel, data = CAMERAS[cam](t)
                    img = Image.open(io.BytesIO(data))
                    mes = mesures(img, cam)
                    vign = img.convert("RGB")
                    vign.thumbnail((320, 240))
                    rel = f"vignettes/{cam}/{d:%Y}/{d.isoformat()}_{moment}.jpg"
                    os.makedirs(os.path.dirname(os.path.join(RACINE, rel)), exist_ok=True)
                    vign.save(os.path.join(RACINE, rel), quality=80)
                    w.writerow([d.isoformat(), cam, moment, t_reel.strftime("%H:%M"), rel] + mes)
                    n += 1
                except Exception as e:
                    w.writerow([d.isoformat(), cam, moment, t.strftime("%H:%M"), ""] + [""] * 8)
                    print(f"[manque] {d} {cam} {moment} : {e}", file=sys.stderr)
                f.flush()
                time.sleep(1)
        d += dt.timedelta(days=1)
    f.close()
    print(f"{n} images collectées")


if __name__ == "__main__":
    cams = sys.argv[3].split(",") if len(sys.argv) > 3 else ("pdd", "montdore")
    main(sys.argv[1], sys.argv[2], cams)
