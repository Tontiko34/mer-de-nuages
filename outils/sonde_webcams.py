"""Sonde ponctuelle : repère les adresses d'images et d'archives des webcams suivies."""
import json, re, urllib.request, os
UA = {"User-Agent": "Mozilla/5.0 (alerte-mer-de-nuages; usage personnel)"}
os.makedirs("sonde", exist_ok=True)
rapport = {}

def get(url, binaire=False):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
            data = r.read()
            return {"status": r.status, "type": r.headers.get("Content-Type"), "taille": len(data),
                    "data": data if binaire else data.decode("utf-8", "replace")}
    except Exception as e:
        return {"erreur": str(e)}

pages = {
    "bergfex_robots": "https://www.bergfex.fr/robots.txt",
    "bergfex": "https://www.bergfex.fr/sommer/clermont-auvergne-metropole/webcams/c25369/",
    "webcamhd_robots": "https://app.webcam-hd.com/robots.txt",
    "webcamhd": "https://app.webcam-hd.com/sancy/mont-dore-sommet-station?date=2026-11-24&time=08:00",
    "webcamhd_now": "https://app.webcam-hd.com/sancy/mont-dore-sommet-station",
}
for nom, url in pages.items():
    r = get(url)
    txt = r.pop("data", "")
    if nom.endswith("robots"):
        r["contenu"] = txt[:3000]
    else:
        with open(f"sonde/{nom}.html", "w", encoding="utf-8") as f:
            f.write(txt)
        r["images"] = sorted(set(re.findall(r'https?://[^"\'\s<>()]+?\.(?:jpe?g|webp|png)(?:\?[^"\'\s<>()]*)?', txt)))[:60]
        r["api"] = sorted(set(re.findall(r'["\'](/api/[^"\']+|https?://[^"\']*api[^"\']*)["\']', txt)))[:60]
        r["scripts"] = sorted(set(re.findall(r'<script[^>]+src="([^"]+)"', txt)))[:30]
        r["mentions"] = [m[:200] for m in re.findall(r'[^<>]{0,80}(?:©|copyright|Copyright|OPGC|Observatoire)[^<>]{0,80}', txt)][:20]
    rapport[nom] = r

# Essais de téléchargement d'images candidates
essais = {"webcamhd_panorama": "https://app.webcam-hd.com/api/webcam-image/panorama/sancy_sommet-station/mega_sancy_sommet-station.jpg?config=8579"}
for nom in ("bergfex",):
    for u in rapport[nom].get("images", []):
        if any(k in u for k in ("webcam", "cam", "images")) and not u.endswith(".png"):
            essais[f"{nom}_{len(essais)}"] = u
for nom, u in list(essais.items())[:12]:
    r = get(u, binaire=True)
    data = r.pop("data", b"")
    if data and "image" in (r.get("type") or ""):
        ext = "webp" if "webp" in r["type"] else "jpg"
        with open(f"sonde/{nom}.{ext}", "wb") as f:
            f.write(data)
    r["url"] = u
    rapport[f"img_{nom}"] = r

with open("sonde/rapport.json", "w", encoding="utf-8") as f:
    json.dump(rapport, f, ensure_ascii=False, indent=1)
