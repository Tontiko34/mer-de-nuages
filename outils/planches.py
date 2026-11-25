"""
Assemble les vignettes en planches numérotées pour l'étiquetage visuel.

Usage : python outils/planches.py CAMERA MOMENT DOSSIER_SORTIE [ordre=date|lum] [par_planche=30]
Chaque vignette porte un numéro ; un fichier index.csv fait le lien numéro -> date.
"""
import csv
import os
import sys

from PIL import Image, ImageDraw, ImageFont

RACINE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "etiquetage")


def main(cam, moment, sortie, ordre="date", par_planche=30):
    with open(os.path.join(RACINE, "images.csv"), encoding="utf-8") as f:
        lignes = [r for r in csv.DictReader(f) if r["camera"] == cam and r["moment"] == moment and r["fichier"]]
    if ordre == "lum":
        lignes.sort(key=lambda r: float(r["lum"]))
    else:
        lignes.sort(key=lambda r: r["date"])
    os.makedirs(sortie, exist_ok=True)
    tw, th, cols = (200, 150, 6) if cam == "pdd" else (300, 113, 4)
    try:
        police = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    except OSError:
        police = ImageFont.load_default()
    index = []
    for p in range(0, len(lignes), par_planche):
        lot = lignes[p:p + par_planche]
        rangs = (len(lot) + cols - 1) // cols
        planche = Image.new("RGB", (cols * tw, rangs * th), "black")
        d = ImageDraw.Draw(planche)
        for k, r in enumerate(lot):
            num = p + k
            im = Image.open(os.path.join(RACINE, r["fichier"])).convert("RGB")
            im.thumbnail((tw - 4, th - 4))
            x, y = (k % cols) * tw, (k // cols) * th
            planche.paste(im, (x + 2, y + 2))
            d.rectangle((x + 2, y + 2, x + 112, y + 20), fill="black")
            d.text((x + 5, y + 3), f"{num} {r['date'][2:]}", fill="yellow", font=police)
            index.append((num, r["date"], r["fichier"]))
        planche.save(os.path.join(sortie, f"{cam}_{moment}_{p // par_planche:02d}.jpg"), quality=85)
    with open(os.path.join(sortie, f"index_{cam}_{moment}.csv"), "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([("num", "date", "fichier")] + index)
    print(f"{len(lignes)} vignettes, {(len(lignes) + par_planche - 1) // par_planche} planches")


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0], a[1], a[2], a[3] if len(a) > 3 else "date", int(a[4]) if len(a) > 4 else 30)
