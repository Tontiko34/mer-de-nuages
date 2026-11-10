# Alerte mer de nuages – Chaîne des Puys & Monts Dore

Prévoit à J+1 / J+2 les créneaux où une mer de nuages recouvre la Limagne, le Val d'Allier ou la Haute-Dordogne
**sous** le Puy de Dôme, le Puy de la Tâche, le Puy de l'Angle ou le Sancy, et t'envoie une notification sur ton téléphone.

## Principe

Données gratuites **Open-Meteo** (sans clé), trois modèles comparés :
Météo-France AROME/ARPEGE (`meteofrance_seamless`), DWD ICON, ECMWF IFS.

Pour chaque heure et chaque point de vallée, le script reconstruit un profil vertical
(sol + niveaux 1000 → 700 hPa, soit ~150 m → ~3000 m) : température, humidité, nébulosité, altitude réelle du niveau.
Il calcule ensuite pour chaque sommet un **score 0–100** :

| Facteur | Condition favorable | Effet |
|---|---|---|
| Nuages sous le sommet | nébulosité ≥ 90 % (ou brouillard en vallée) | 40 % → 0, 90 % → plein score |
| Ciel au sommet | nébulosité ≤ 10 % à l'altitude du sommet et 400 m au-dessus | 60 % → score nul |
| Inversion | température qui augmente avec l'altitude | atmosphère standard : score réduit de ~60 % |
| Vent à 850 hPa (~1500 m) | < 20 km/h | ≥ 50 km/h : score réduit de 40 % |
| Haut de la couche | doit rester ≥ 50 m sous le sommet | sinon score × 0,2 (tu es dans le nuage) |

Score final = médiane des modèles ; la colonne « Modèles ≥ 50 » indique leur accord (bon indice de confiance).
Pour chaque jour : meilleur créneau diurne (moyenne glissante sur 3 h) et score autour du lever du soleil.

## 1. Recevoir les notifications : ntfy (gratuit, sans compte)

1. Installe l'appli **ntfy** (Android / iOS).
2. « S'abonner à un sujet » → invente un nom difficile à deviner, ex. `mdn-puys-7f3k9q`
   (tout le monde peut lire un sujet dont il connaît le nom, donc pas de nom trop simple).
3. Ce nom est ton `NTFY_TOPIC`.

## 2a. Faire tourner automatiquement sur GitHub (recommandé, gratuit, PC éteint)

1. Crée un dépôt **public** sur github.com (ex. `mer-de-nuages`) et dépose-y tout le contenu de ce dossier,
   y compris le dossier caché `.github/workflows/`.
2. *Settings → Secrets and variables → Actions → New repository secret* : nom `NTFY_TOPIC`, valeur = ton sujet ntfy.
3. *Settings → Pages → Build and deployment → Source* : **GitHub Actions**.
4. Onglet *Actions* → « Alerte mer de nuages » → **Run workflow** pour un premier essai.
5. Le tableau de bord est publié sur `https://<ton-pseudo>.github.io/mer-de-nuages/`.
   Copie cette adresse dans `url_tableau_de_bord` (config.json) : un appui sur la notification ouvrira la page.

Le calcul tourne à ~6h20 et ~17h50 (heure d'hiver). Modifier les lignes `cron` du fichier
`.github/workflows/alerte.yml` pour changer les horaires (elles sont en UTC).
GitHub suspend les tâches planifiées d'un dépôt public sans activité depuis 60 jours : il prévient par mail,
un clic suffit pour réactiver.

## 2b. Ou sur ton PC (Windows)

```bat
python mer_de_nuages.py --topic mdn-puys-7f3k9q
```
Ouvre ensuite `docs\index.html` dans le navigateur.
Pour l'automatiser : Planificateur de tâches → Créer une tâche de base → quotidienne à 18h →
programme `python`, arguments `mer_de_nuages.py --topic mdn-puys-7f3k9q`, « Commencer dans » = ce dossier.

## 3. Calibrer sur tes propres observations

Les seuils sont un premier réglage physique. Le meilleur moyen de les affiner : rejouer des journées
où tu as vu (ou raté) une mer de nuages, avec les prévisions archivées d'Open-Meteo :

```bat
python mer_de_nuages.py --debut 2025-12-01 --fin 2025-12-10 --sortie rejeu.html
```

(l'archive remonte à quelques années selon le modèle ; aucune notification n'est envoyée en mode rejeu).
Si le score est trop prudent ou trop optimiste, ajuste dans `mer_de_nuages.py`, fonction `analyser()`,
les bornes des lignes `s_dessous`, `s_sommet`, `s_inversion`, `s_vent`.

## Autres alertes

Le même calcul surveille aussi (réglages dans `config.json`, rubrique `evenements`) :

| Alerte | Ce qui est calculé | Seuil par défaut |
|---|---|---|
| 🌅 Ciel coloré | nuages d'altitude au-dessus de toi (ni trop peu, ni couvert) × horizon dégagé côté soleil (à 60 et 150 km) × pas de plafond bas. Moyenne des modèles, ±1 h | score ≥ 55 et au moins 1 modèle favorable |
| ✨ Ciel étoilé | heures de nuit noire (soleil < −18°, Lune couchée ou éclairée < 20 %) sans nuages au-dessus du col de la Croix-Morand, air sec | score ≥ 75 (≈ 3–4 h de ciel noir) et au moins 2 modèles d'accord |
| ❄️ Neige | pluie × température à l'altitude du Puy de Dôme et du Sancy (≈ 1 cm par mm d'eau sous 0 °C) | ≥ 1 cm (médiane des modèles) ; un épisode de plusieurs jours = une seule alerte |

**Anti-doublons** : chaque événement n'est signalé qu'une fois (score qui progresse de 15 points ou plus, ou neige
qui double : nouvelle alerte). Les événements d'un même calcul arrivent groupés dans une seule notification.
Le fichier `etat_alertes.json` garde la mémoire des alertes envoyées.

Sur le rejeu du 1er octobre au 9 novembre 2026, avec ces seuils : 10 jours de mer de nuages, 3 ciels colorés,
4 nuits étoilées et 4 épisodes neigeux, soit ~18 notifications en 38 jours. Les seuils « ciel coloré » et
« ciel étoilé » ont été fixés pour ne retenir que les meilleurs cas : à affiner avec tes observations.

## Réglages (config.json)

| Clé | Rôle |
|---|---|
| `modeles` | modèles Open-Meteo interrogés (retire-en un s'il renvoie une erreur) |
| `seuil_alerte` | score minimal de mer de nuages pour notifier (60 par défaut) |
| `points_vallee` | points où l'on regarde le profil (lat/lon) — ajoute-en librement |
| `sommets` | nom, altitude, et vallées visibles depuis chaque sommet |

## Limites

* Les modèles ne disposent que de niveaux de pression espacés de 250 à 500 m : le haut de la couche est donné à ±200 m.
  Les situations limites (couche à 1400–1600 m pour le Puy de Dôme) restent incertaines.
* Les stratus d'inversion sont un point faible connu des modèles (ils les dissipent souvent trop tôt) :
  un score de 30–50 avec un fort accord sur l'inversion mérite parfois un coup d'œil aux webcams.
* AROME ne couvre que ~2 jours ; au-delà, `meteofrance_seamless` bascule sur ARPEGE (moins fin).
