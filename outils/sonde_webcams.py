"""Sonde ponctuelle (2) : profondeur des archives Webcam-HD et accès bergfex."""
import json, re, time, urllib.request, os
UA = {"User-Agent": "Mozilla/5.0 (alerte-mer-de-nuages; usage personnel)"}
os.makedirs("sonde", exist_ok=True)
rapport = {}

def get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        return str(e), ""

BASE = "https://skol-webcams-archives.s3.fr-par.scw.cloud/"
for jour in ["2026/11/24", "2026/11/13", "2026/10/31", "2026/01/15", "2025/12/15", "2025/01/15", "2024/12/15", "2023/12/15", "2022/12/15"]:
    st, txt = get(f"{BASE}?list-type=2&prefix=archives/sancy_sommet-station/1080/{jour}/")
    cles = re.findall(r"<Key>([^<]+)</Key>", txt)
    rapport[jour] = {"statut": st, "nb": len(cles), "exemples": cles[:3] + cles[-2:], "debut": txt[:300] if not cles else ""}
    time.sleep(1)
# Dossiers de premier niveau (autres webcams du même hébergeur, autres résolutions)
st, txt = get(f"{BASE}?list-type=2&prefix=archives/&delimiter=/&max-keys=1000")
rapport["webcams_hebergees"] = {"statut": st, "prefixes": re.findall(r"<Prefix>([^<]+)</Prefix>", txt)[:300]}
time.sleep(5)
for nom, url in {"bergfex": "https://www.bergfex.fr/sommer/clermont-auvergne-metropole/webcams/c25369/",
                 "bergfex_robots": "https://www.bergfex.fr/robots.txt"}.items():
    st, txt = get(url)
    rapport[nom] = {"statut": st, "images": sorted(set(re.findall(r'https?://[^"\'\s<>()]+?\.(?:jpe?g|webp)', txt)))[:30],
                    "mentions": re.findall(r'[^<>]{0,60}(?:©|OPGC|Observatoire)[^<>]{0,60}', txt)[:10],
                    "robots_extrait": txt[:1500] if "robots" in nom else ""}
    time.sleep(5)
with open("sonde/rapport2.json", "w", encoding="utf-8") as f:
    json.dump(rapport, f, ensure_ascii=False, indent=1)
