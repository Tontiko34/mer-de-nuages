"""Sonde ponctuelle (3) : début des archives + images des jours de test."""
import json, re, time, urllib.request, os
UA = {"User-Agent": "Mozilla/5.0 (alerte-mer-de-nuages; usage personnel)"}
BASE = "https://skol-webcams-archives.s3.fr-par.scw.cloud/"
CAM = "sancy_sommet-station"
os.makedirs("sonde", exist_ok=True)
rapport = {}

def get(url, binaire=False):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        d = r.read()
        return d if binaire else d.decode("utf-8", "replace")

def cles(prefixe):
    return re.findall(r"<Key>([^<]+)</Key>", get(f"{BASE}?list-type=2&prefix={prefixe}&max-keys=1000"))

# Premier mois disponible
for an, mois in [(2025, m) for m in range(2, 13)]:
    n = len(cles(f"archives/{CAM}/1080/{an}/{mois:02d}/"))
    rapport[f"{an}-{mois:02d}"] = n
    time.sleep(0.5)

# Images du matin (~8 h locale) des jours de test
for jour in ["2026/10/31", "2026/11/12", "2026/11/13"]:
    liste = [k for k in cles(f"archives/{CAM}/small/{jour}/")]
    choix = min(liste, key=lambda k: abs(int(re.search(r"T(\d{4})", k).group(1)) - 715)) if liste else None
    if choix:
        with open("sonde/" + choix.split("/")[-1], "wb") as f:
            f.write(get(BASE + choix, binaire=True))
    rapport[jour] = {"nb": len(liste), "heures": [re.search(r"T(\d{4})", k).group(1) for k in liste], "choix": choix}
    time.sleep(0.5)
with open("sonde/rapport3.json", "w", encoding="utf-8") as f:
    json.dump(rapport, f, ensure_ascii=False, indent=1)
