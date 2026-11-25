import time, urllib.request, json
UA={"User-Agent":"alerte-mer-de-nuages (usage personnel, accord OPGC)"}
res=[]
for h in ["0802","0812","0822","1002","1012"]:
    for d in ["2026/01/10","2026/02/10"]:
        u=f"https://wwwobs.univ-bpclermont.fr/opgc/Archive-webcam/pdd/{d}/webcampdd-{d.replace('/','')}{h}.jpg"
        t=time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=60) as r: n=len(r.read()); s=r.status
        except Exception as e: n=0; s=str(e)
        res.append([u[-30:],s,n,round(time.time()-t,2)])
t=time.time()
try:
    urllib.request.urlopen(urllib.request.Request("https://skol-webcams-archives.s3.fr-par.scw.cloud/?list-type=2&prefix=archives/sancy_sommet-station/small/2026/01/10/",headers=UA),timeout=60).read(); res.append(["s3 list",200,0,round(time.time()-t,2)])
except Exception as e: res.append(["s3 list",str(e),0,round(time.time()-t,2)])
open("vitesse.json","w").write(json.dumps(res,indent=1))
