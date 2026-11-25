"""Sonde ponctuelle (5) : diaporama d'archives OPGC pour les jours de test."""
import json, re, time, urllib.request, urllib.parse, os
UA = {"User-Agent": "Mozilla/5.0 (alerte-mer-de-nuages; usage personnel)"}
B = "https://wwwobs.univ-bpclermont.fr/opgc/"
os.makedirs("sonde", exist_ok=True)
rapport = {}

def req(url, data=None, binaire=False):
    try:
        r = urllib.request.Request(url, headers=UA, data=urllib.parse.urlencode(data).encode() if data else None)
        with urllib.request.urlopen(r, timeout=40) as rep:
            d = rep.read()
            return rep.status, (d if binaire else d.decode("latin-1", "replace"))
    except Exception as e:
        return str(e), (b"" if binaire else "")

for (a, m, j) in [("2026", "10", "31"), ("2026", "11", "12"), ("2026", "11", "13"), ("2013", "01", "15")]:
    st, txt = req(B + "diapocam.php", {"camera": "pdd", "annee": a, "mois": m, "jour": j, "Submit": "Valider"})
    imgs = sorted(set(re.findall(r'["\']([^"\']+\.jpe?g)["\']', txt)))
    rapport[f"{a}-{m}-{j}"] = {"statut": st, "nb": len(imgs), "images": imgs[:8] + ["..."] + imgs[-4:],
                               "extrait": re.sub(r"\s+", " ", txt)[:1500] if len(imgs) < 3 else ""}
    # image la plus proche de 8 h locale
    cand = [i for i in imgs if re.search(r"(0[78])[-_:h]?\d\d", i)]
    if cand:
        u = urllib.parse.urljoin(B + "diapocam.php", cand[0])
        s2, d = req(u, binaire=True)
        if isinstance(d, bytes) and d:
            open(f"sonde/opgc_{a}{m}{j}_" + os.path.basename(u), "wb").write(d)
        rapport[f"{a}-{m}-{j}"]["telecharge"] = [u, s2, len(d)]
    time.sleep(2)
with open("sonde/rapport5.json", "w", encoding="utf-8") as f:
    json.dump(rapport, f, ensure_ascii=False, indent=1)
