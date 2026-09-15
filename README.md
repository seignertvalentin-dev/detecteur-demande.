# Détecteur de demande — Apify Store

Chaque lundi, ce script interroge l'API publique officielle de l'Apify Store,
mesure la demande réelle par niche et publie un rapport. Objectif : ne construire
un outil que là où des gens l'utilisent déjà, et payent.

## Installation (environ 10 minutes, une seule fois)

1. Créer un dépôt **privé** sur GitHub (par exemple `detecteur-demande`).
2. Y déposer tout le contenu de ce dossier, y compris le dossier caché `.github`.
3. Onglet **Actions** : autoriser les workflows, puis ouvrir « Détecteur de demande »
   et cliquer sur **Run workflow** pour le premier relevé.
4. Le rapport apparaît dans `docs/index.html` (à télécharger et ouvrir dans un
   navigateur). Pour le consulter en ligne : **Settings → Pages → Branch `main` /
   dossier `/docs`**. Sur un dépôt privé, GitHub Pages nécessite un compte payant ;
   sinon, gardez le dépôt privé et téléchargez simplement le fichier.

Aucune clé d'API n'est nécessaire. Un relevé prend quelques minutes, largement dans
le quota gratuit de GitHub Actions.

Test local sans réseau : `python detecteur.py --demo` (données fictives).

## Ce que contient le rapport

| Section | Question à laquelle elle répond |
|---|---|
| Niches classées | Où la demande est forte, peu disputée, et déjà payante ? |
| Leaders fragiles | Quels outils populaires déçoivent leurs utilisateurs ? |
| Nouveaux outils qui décollent | Quels besoins émergent en ce moment ? |

Les CSV (`docs/*.csv`) contiennent les mêmes données pour un tri dans un tableur.
L'historique compressé est dans `data/snapshots/` ; à partir du deuxième relevé,
la colonne « Évolution » compare la demande à la semaine précédente.

## Le score, en clair

```
score = 10 × log10(1 + demande) × (1 + faiblesse) × (0,5 + 0,5 × part_payante) / √(1 + actifs)
```

- **demande** : somme des utilisateurs sur 30 jours des outils trouvés pour le mot-clé
- **faiblesse** (0 à 1) : +0,4 si le leader est mal noté, +0,3 s'il échoue souvent,
  +0,3 s'il est obsolète ou en maintenance
- **part_payante** : part de la demande captée par des outils payants
- **actifs** : concurrents au-dessus du seuil d'utilisateurs mensuels

Le logarithme évite qu'une niche géante écrase tout ; la racine pénalise la
concurrence sans l'interdire. Tous les seuils et poids sont modifiables dans
`config.json`.

## Personnaliser

- Ajouter des niches à tester : section `mots_cles` de `config.json`.
- Durcir ou assouplir les critères : section `seuils`.

## Limites à garder en tête

- Les utilisateurs mesurent l'usage, pas le chiffre d'affaires.
- La recherche par mot-clé peut remonter des outils hors sujet : vérifier à la main
  le haut du classement avant de décider.
- L'API renvoie au maximum 1 000 résultats par requête ; le balayage par catégorie
  couvre donc les outils les plus populaires, pas la totalité du store.
- Certains champs (note, taux de succès) peuvent être absents ; le script les
  traite comme inconnus plutôt que comme mauvais.
