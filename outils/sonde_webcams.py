"""Sonde ponctuelle (4) : archives des webcams OPGC (Puy de Dôme sommet, Cézeaux)."""
import json, re, time, urllib.request, urllib.parse, os
UA = {"User-Agent": "Mozilla/5.0 (alerte-mer-de-nuages; usage personnel)"}
os.makedirs("sonde", exist_ok=True)
B = "https://wwwobs.univ-bpclermont.fr/opgc/"
rapport = {}

def get(url, binaire=False):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
            d = r.read()
            return r.status, (d if binaire else d.decode("latin-1", "replace"))
    except Exception as e:
        return str(e), b"" if binaire else ""

for nom, url in {"page": B + "webcam.php", "mentions": B + "mentions.php"}.items():
    st, txt = get(url)
    with open(f"sonde/opgc_{nom}.html", "w", encoding="utf-8") as f:
        f.write(txt)
    rapport[nom] = {"statut": st, "liens": sorted(set(re.findall(r'(?:href|src|action)="([^"]+)"', txt)))[:120],
                    "texte": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", txt))[:3000]}
    time.sleep(1)

# Suivre les liens d'archives trouvés (1 niveau, quelques-uns)
suivis = [l for l in rapport["page"]["liens"] if re.search(r"archiv|annee|year|mois|jour|\.php\?", l, re.I)][:8]
for l in suivis:
    url = urllib.parse.urljoin(B + "webcam.php", l)
    st, txt = get(url)
    rapport["suivi:" + l] = {"statut": st, "liens": sorted(set(re.findall(r'(?:href|src)="([^"]+)"', txt)))[:80],
                             "texte": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", txt))[:800]}
    time.sleep(1)
st, img = get(B + "webcampdd.jpg", binaire=True)
if isinstance(img, bytes) and img:
    open("sonde/opgc_webcampdd.jpg", "wb").write(img)
rapport["image_live"] = {"statut": st, "taille": len(img)}
with open("sonde/rapport4.json", "w", encoding="utf-8") as f:
    json.dump(rapport, f, ensure_ascii=False, indent=1)
