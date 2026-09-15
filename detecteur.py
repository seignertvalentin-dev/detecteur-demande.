#!/usr/bin/env python3
"""
Détecteur de demande — Apify Store
==================================

Interroge l'API publique officielle de l'Apify Store (GET /v2/store), mesure la
demande réelle (utilisateurs sur 30 jours) par niche, repère les leaders fragiles
et les nouveaux outils qui décollent, puis génère un rapport.

Aucune dépendance externe : Python 3.10+ standard.

Usage :
    python detecteur.py            # collecte réelle + rapport
    python detecteur.py --demo     # données FICTIVES pour tester le rapport
"""

from __future__ import annotations

import argparse
import csv
import gzip
import html
import json
import math
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

RACINE = Path(__file__).resolve().parent
DOSSIER_SNAPSHOTS = RACINE / "data" / "snapshots"
DOSSIER_SORTIE = RACINE / "docs"
MAINTENANT = datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Modèle
# --------------------------------------------------------------------------

@dataclass
class Outil:
    id: str
    titre: str
    auteur: str
    url: str
    categories: list[str]
    utilisateurs_30j: int
    utilisateurs_total: int
    note: float | None
    nb_avis: int
    taux_succes_30j: float | None
    modele_prix: str
    cree_le: str | None
    modifie_le: str | None
    avis_statut: str
    requetes: list[str] = field(default_factory=list)

    @property
    def payant(self) -> bool:
        return self.modele_prix not in ("", "FREE")

    def age_jours(self, champ: str) -> int | None:
        valeur = getattr(self, champ)
        if not valeur:
            return None
        try:
            d = datetime.fromisoformat(valeur.replace("Z", "+00:00"))
        except ValueError:
            return None
        return (MAINTENANT - d).days


def _premier(*valeurs):
    for v in valeurs:
        if v is not None:
            return v
    return None


def normaliser(item: dict) -> Outil:
    """Convertit un item de l'API en Outil, de façon défensive (champs optionnels)."""
    stats = item.get("stats") or {}
    prix = item.get("currentPricingInfo") or {}

    taux = None
    runs = stats.get("publicActorRunStats30Days") or {}
    if isinstance(runs, dict):
        total = runs.get("TOTAL")
        if total is None:
            total = sum(v for v in runs.values() if isinstance(v, (int, float)))
        if total:
            taux = (runs.get("SUCCEEDED") or 0) / total

    auteur = item.get("username") or ""
    nom = item.get("name") or ""
    note = _premier(item.get("actorReviewRating"), stats.get("actorReviewRating"))

    return Outil(
        id=item.get("id") or f"{auteur}/{nom}",
        titre=item.get("title") or nom,
        auteur=auteur,
        url=f"https://apify.com/{auteur}/{nom}",
        categories=list(item.get("categories") or []),
        utilisateurs_30j=int(_premier(stats.get("totalUsers30Days"), item.get("totalUsers30Days"), 0)),
        utilisateurs_total=int(_premier(stats.get("totalUsers"), item.get("totalUsers"), 0)),
        note=float(note) if note is not None else None,
        nb_avis=int(_premier(item.get("actorReviewCount"), stats.get("actorReviewCount"), 0)),
        taux_succes_30j=taux,
        modele_prix=str(prix.get("pricingModel") or "FREE"),
        cree_le=item.get("createdAt"),
        modifie_le=item.get("modifiedAt"),
        avis_statut=str(item.get("notice") or "NONE"),
    )


# --------------------------------------------------------------------------
# Collecte
# --------------------------------------------------------------------------

def appeler_api(base: str, params: dict, essais: int = 3) -> dict:
    url = f"{base}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "detecteur-demande/1.0"})
    for tentative in range(1, essais + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if tentative == essais:
                raise
            print(f"  ! tentative {tentative} échouée ({e}), nouvel essai…", file=sys.stderr)
            time.sleep(2 * tentative)
    return {}


def collecter(cfg: dict) -> tuple[dict[str, Outil], dict[str, list[str]]]:
    """Renvoie (outils par id, ids par requête de niche)."""
    base, pause = cfg["api_base"], cfg["pause_secondes"]
    outils: dict[str, Outil] = {}
    niches: dict[str, list[str]] = {}

    def enregistrer(item: dict, requete: str | None):
        o = normaliser(item)
        existant = outils.setdefault(o.id, o)
        if requete and requete not in existant.requetes:
            existant.requetes.append(requete)
        return existant.id

    # 1) Niches par mot-clé (classement par pertinence)
    for groupe, mots in cfg["mots_cles"].items():
        for mot in mots:
            if mot in niches:
                continue  # mot-clé déjà interrogé dans un autre groupe
            print(f"[mot-clé] {groupe} / {mot}")
            data = appeler_api(base, {
                "search": mot, "sortBy": "relevance",
                "limit": cfg["resultats_par_mot_cle"], "offset": 0,
            }).get("data", {})
            niches[mot] = [enregistrer(it, mot) for it in data.get("items", [])]
            time.sleep(pause)

    # 2) Balayage large par catégorie (popularité) pour les leaders fragiles
    for cat in cfg["categories"]:
        offset = 0
        while offset < cfg["max_par_categorie"]:
            print(f"[catégorie] {cat} offset={offset}")
            data = appeler_api(base, {
                "category": cat, "sortBy": "popularity", "limit": 100, "offset": offset,
            }).get("data", {})
            items = data.get("items", [])
            for it in items:
                enregistrer(it, None)
            if len(items) < 100:
                break
            offset += 100
            time.sleep(pause)

    return outils, niches


def donnees_demo(cfg: dict) -> tuple[dict[str, Outil], dict[str, list[str]]]:
    """Données FICTIVES, uniquement pour vérifier que le rapport fonctionne."""
    rnd = random.Random(42)
    outils: dict[str, Outil] = {}
    niches: dict[str, list[str]] = {}
    n = 0
    for mots in cfg["mots_cles"].values():
        for mot in mots:
            ids = []
            taille_marche = rnd.choice([30, 200, 1500, 8000])
            for rang in range(rnd.randint(3, 25)):
                n += 1
                u30 = int(taille_marche * rnd.paretovariate(1.6) / (rang + 1) ** 1.3)
                cree = MAINTENANT - timedelta(days=rnd.randint(5, 900))
                modif = cree + timedelta(days=rnd.randint(0, max(1, (MAINTENANT - cree).days)))
                o = Outil(
                    id=f"demo{n}", titre=f"{mot.title()} outil {rang + 1}",
                    auteur=f"dev{rnd.randint(1, 400)}", url="https://apify.com/store",
                    categories=["DEMO"], utilisateurs_30j=u30,
                    utilisateurs_total=u30 * rnd.randint(2, 12),
                    note=round(rnd.uniform(2.5, 5.0), 1) if rnd.random() < 0.6 else None,
                    nb_avis=rnd.randint(0, 40),
                    taux_succes_30j=round(rnd.uniform(0.6, 1.0), 3),
                    modele_prix=rnd.choice(["FREE", "PAY_PER_EVENT", "PRICE_PER_DATASET_ITEM"]),
                    cree_le=cree.isoformat(), modifie_le=modif.isoformat(),
                    avis_statut=rnd.choice(["NONE"] * 9 + ["UNDER_MAINTENANCE"]),
                    requetes=[mot],
                )
                outils[o.id] = o
                ids.append(o.id)
            niches[mot] = ids
    return outils, niches


# --------------------------------------------------------------------------
# Analyse
# --------------------------------------------------------------------------

def faiblesses(o: Outil, s: dict) -> list[str]:
    raisons = []
    if o.note is not None and o.nb_avis >= s["avis_min_pour_note"] and o.note < s["note_faible_sous"]:
        raisons.append(f"note {o.note:.1f}/5 ({o.nb_avis} avis)")
    if o.taux_succes_30j is not None and o.taux_succes_30j < s["succes_faible_sous"]:
        raisons.append(f"{o.taux_succes_30j:.0%} d'exécutions réussies")
    age_modif = o.age_jours("modifie_le")
    if o.avis_statut not in ("", "NONE"):
        raisons.append(f"statut {o.avis_statut}")
    elif age_modif is not None and age_modif > s["obsolete_apres_jours"]:
        raisons.append(f"pas mis à jour depuis {age_modif} j")
    return raisons


@dataclass
class Niche:
    requete: str
    groupe: str
    demande_30j: int
    nb_outils: int
    nb_actifs: int
    part_leader: float
    part_payante: float
    faiblesse: float
    score: float
    leader: str
    leader_url: str
    raisons_leader: list[str]
    croissance: float | None


def score_niche(demande: int, nb_actifs: int, faiblesse: float, part_payante: float) -> float:
    """
    score = 10 × log10(1 + demande) × (1 + faiblesse) × (0,5 + 0,5 × part_payante) / √(1 + nb_actifs)

    - demande       : somme des utilisateurs sur 30 jours dans la niche
    - faiblesse     : 0 à 1, fragilité du leader (note, échecs, obsolescence)
    - part_payante  : part de la demande captée par des outils payants (preuve que l'on paie)
    - nb_actifs     : concurrents ayant au moins le seuil d'utilisateurs actifs
    """
    return 10 * math.log10(1 + demande) * (1 + faiblesse) * (0.5 + 0.5 * part_payante) / math.sqrt(1 + nb_actifs)


def analyser(outils, niches, cfg, precedent: dict | None):
    s, p = cfg["seuils"], cfg["poids_faiblesse"]
    groupe_de = {m: g for g, mots in cfg["mots_cles"].items() for m in mots}
    prec_niches = (precedent or {}).get("niches_demande", {})

    resultats: list[Niche] = []
    for requete, ids in niches.items():
        membres = [outils[i] for i in ids if i in outils]
        if not membres:
            continue
        demande = sum(o.utilisateurs_30j for o in membres)
        actifs = [o for o in membres if o.utilisateurs_30j >= s["actif_min_utilisateurs_30j"]]
        leader = max(membres, key=lambda o: o.utilisateurs_30j)
        payante = sum(o.utilisateurs_30j for o in membres if o.payant)

        raisons = faiblesses(leader, s)
        f = 0.0
        if any(r.startswith("note") for r in raisons):
            f += p["note_basse"]
        if any("exécutions" in r for r in raisons):
            f += p["echecs_frequents"]
        if any(r.startswith(("statut", "pas mis")) for r in raisons):
            f += p["obsolete_ou_maintenance"]
        f = min(f, 1.0)

        part_payante = payante / demande if demande else 0.0
        croissance = None
        if requete in prec_niches and prec_niches[requete] > 0:
            croissance = demande / prec_niches[requete] - 1

        resultats.append(Niche(
            requete=requete, groupe=groupe_de.get(requete, "—"),
            demande_30j=demande, nb_outils=len(membres), nb_actifs=len(actifs),
            part_leader=(leader.utilisateurs_30j / demande) if demande else 0.0,
            part_payante=part_payante, faiblesse=f,
            score=score_niche(demande, len(actifs), f, part_payante),
            leader=leader.titre, leader_url=leader.url, raisons_leader=raisons,
            croissance=croissance,
        ))
    resultats.sort(key=lambda n: n.score, reverse=True)

    fragiles = []
    for o in outils.values():
        if o.utilisateurs_30j >= s["faible_min_utilisateurs_30j"]:
            r = faiblesses(o, s)
            if r:
                fragiles.append((o, r))
    fragiles.sort(key=lambda x: x[0].utilisateurs_30j, reverse=True)

    montants = []
    for o in outils.values():
        age = o.age_jours("cree_le")
        if age is not None and age <= s["montant_age_max_jours"] and o.utilisateurs_30j >= s["montant_min_utilisateurs_30j"]:
            montants.append((o, o.utilisateurs_30j / max(age, 1)))
    montants.sort(key=lambda x: x[1], reverse=True)

    return resultats, fragiles, montants


# --------------------------------------------------------------------------
# Rapport
# --------------------------------------------------------------------------

def fmt_int(n: int) -> str:
    return f"{n:,}".replace(",", "\u202f")


def fmt_pct(x: float | None) -> str:
    return "—" if x is None else f"{x:+.0%}" if x < 0 or x > 0 else "0 %"


def ecrire_csv(niches, fragiles, montants):
    DOSSIER_SORTIE.mkdir(parents=True, exist_ok=True)
    with open(DOSSIER_SORTIE / "niches.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rang", "requete", "groupe", "score", "demande_30j", "nb_outils", "nb_actifs",
                    "part_leader", "part_payante", "faiblesse_leader", "croissance", "leader", "leader_url", "raisons"])
        for i, n in enumerate(niches, 1):
            w.writerow([i, n.requete, n.groupe, round(n.score, 2), n.demande_30j, n.nb_outils, n.nb_actifs,
                        round(n.part_leader, 3), round(n.part_payante, 3), round(n.faiblesse, 2),
                        "" if n.croissance is None else round(n.croissance, 3),
                        n.leader, n.leader_url, " ; ".join(n.raisons_leader)])
    with open(DOSSIER_SORTIE / "leaders_fragiles.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["titre", "url", "utilisateurs_30j", "modele_prix", "raisons"])
        for o, r in fragiles:
            w.writerow([o.titre, o.url, o.utilisateurs_30j, o.modele_prix, " ; ".join(r)])
    with open(DOSSIER_SORTIE / "nouveaux_qui_montent.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["titre", "url", "utilisateurs_30j", "age_jours", "utilisateurs_par_jour_d_age", "modele_prix"])
        for o, v in montants:
            w.writerow([o.titre, o.url, o.utilisateurs_30j, o.age_jours("cree_le"), round(v, 2), o.modele_prix])


def ecrire_html(niches, fragiles, montants, nb_outils, demo: bool):
    e = html.escape
    date = MAINTENANT.strftime("%d/%m/%Y")
    top = niches[0] if niches else None

    def barre(valeur, maxi):
        largeur = 0 if maxi <= 0 else max(2, round(100 * math.log10(1 + valeur) / math.log10(1 + maxi)))
        return f'<span class="barre"><span style="width:{largeur}%"></span></span>'

    max_dem = max((n.demande_30j for n in niches), default=0)
    lignes = []
    for i, n in enumerate(niches, 1):
        alerte = f'<p class="raison">{e(" ; ".join(n.raisons_leader))}</p>' if n.raisons_leader else ""
        lignes.append(f"""
        <tr>
          <td class="num">{i}</td>
          <td><strong>{e(n.requete)}</strong><p class="groupe">{e(n.groupe)}</p></td>
          <td class="num fort">{n.score:.1f}</td>
          <td class="num">{barre(n.demande_30j, max_dem)}<span>{fmt_int(n.demande_30j)}</span></td>
          <td class="num">{n.nb_actifs} / {n.nb_outils}</td>
          <td class="num">{n.part_payante:.0%}</td>
          <td class="num">{fmt_pct(n.croissance)}</td>
          <td><a href="{e(n.leader_url)}">{e(n.leader)}</a> <span class="groupe">({n.part_leader:.0%})</span>{alerte}</td>
        </tr>""")

    lignes_f = "".join(
        f'<tr><td><a href="{e(o.url)}">{e(o.titre)}</a></td><td class="num">{fmt_int(o.utilisateurs_30j)}</td>'
        f'<td>{e(o.modele_prix)}</td><td class="raison">{e(" ; ".join(r))}</td></tr>'
        for o, r in fragiles[:40]
    ) or '<tr><td colspan="4">Aucun leader fragile au-dessus du seuil cette semaine.</td></tr>'

    lignes_m = "".join(
        f'<tr><td><a href="{e(o.url)}">{e(o.titre)}</a></td><td class="num">{fmt_int(o.utilisateurs_30j)}</td>'
        f'<td class="num">{o.age_jours("cree_le")} j</td><td class="num">{v:.1f}</td><td>{e(o.modele_prix)}</td></tr>'
        for o, v in montants[:30]
    ) or '<tr><td colspan="5">Aucun nouvel outil au-dessus du seuil cette semaine.</td></tr>'

    bandeau = ('<p class="demo">Données fictives générées pour tester le rapport. '
               'Lancez le script sans <code>--demo</code> pour les vraies mesures.</p>') if demo else ""

    accroche = ""
    if top:
        accroche = f"""
        <section class="accroche">
          <p class="etiquette">Niche la mieux placée cette semaine</p>
          <h1>{e(top.requete)}</h1>
          <p class="resume">{fmt_int(top.demande_30j)} utilisateurs sur 30 jours, {top.nb_actifs} concurrent(s) actif(s),
          {top.part_payante:.0%} de la demande sur des outils payants.
          Leader : <a href="{e(top.leader_url)}">{e(top.leader)}</a>{(" — " + e(" ; ".join(top.raisons_leader))) if top.raisons_leader else ""}.</p>
        </section>"""

    page = f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Détecteur de demande — {date}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;600;800&display=swap" rel="stylesheet">
<style>
:root {{ --fond:#EDF1F5; --encre:#15253A; --acier:#5A6B80; --trait:#C9D3DE; --signal:#C77A00; --vert:#2D7456; --alerte:#9A3B2E; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --fond:#0F1A27; --encre:#E6ECF2; --acier:#96A6B8; --trait:#2A3A4D; --signal:#F0A93B; --vert:#6FC39C; --alerte:#E88A7B; }}
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--fond); color:var(--encre); font:16px/1.55 "Public Sans", system-ui, sans-serif; }}
main {{ max-width:1180px; margin:0 auto; padding:40px 24px 80px; }}
a {{ color:inherit; text-decoration-color:var(--signal); text-underline-offset:3px; }}
a:focus-visible {{ outline:2px solid var(--signal); outline-offset:2px; }}
.meta {{ color:var(--acier); margin:0 0 32px; }}
.demo {{ border:2px solid var(--alerte); color:var(--alerte); padding:10px 14px; font-weight:600; }}
.accroche {{ border-left:6px solid var(--signal); padding:4px 0 4px 22px; margin:0 0 48px; }}
.etiquette {{ margin:0; color:var(--acier); }}
.accroche h1 {{ font-size:clamp(40px,7vw,76px); line-height:1; font-weight:800; margin:6px 0 14px; letter-spacing:-0.02em; }}
.resume {{ max-width:70ch; margin:0; font-size:18px; }}
h2 {{ font-size:22px; font-weight:800; margin:56px 0 6px; }}
.aide {{ color:var(--acier); max-width:75ch; margin:0 0 16px; }}
.tableau {{ overflow-x:auto; }}
table {{ width:100%; border-collapse:collapse; font-size:15px; }}
th {{ text-align:left; font-weight:600; color:var(--acier); border-bottom:2px solid var(--encre); padding:8px 10px; white-space:nowrap; }}
td {{ border-bottom:1px solid var(--trait); padding:10px; vertical-align:top; }}
.num {{ font-variant-numeric:tabular-nums; white-space:nowrap; }}
.fort {{ font-weight:800; color:var(--signal); }}
.groupe {{ color:var(--acier); margin:2px 0 0; font-size:13px; }}
.raison {{ color:var(--alerte); margin:4px 0 0; font-size:13px; }}
.barre {{ display:inline-block; width:90px; height:8px; background:var(--trait); margin-right:8px; vertical-align:middle; }}
.barre span {{ display:block; height:100%; background:var(--vert); }}
footer {{ margin-top:56px; color:var(--acier); font-size:14px; max-width:80ch; }}
</style>
</head>
<body>
<main>
{bandeau}
<p class="meta">Relevé du {date} sur {fmt_int(nb_outils)} outils de l'Apify Store.</p>
{accroche}

<h2>Niches classées</h2>
<p class="aide">Le score monte avec la demande, la fragilité du leader et la part de demande déjà payante ; il baisse avec le nombre de concurrents actifs. « Actifs » = outils au-dessus du seuil d'utilisateurs mensuels.</p>
<div class="tableau"><table>
<thead><tr><th>#</th><th>Niche</th><th>Score</th><th>Demande 30 j</th><th>Actifs / total</th><th>Part payante</th><th>Évolution</th><th>Leader (part)</th></tr></thead>
<tbody>{"".join(lignes)}</tbody>
</table></div>

<h2>Leaders fragiles</h2>
<p class="aide">Outils très utilisés mais mal notés, instables ou délaissés : leurs utilisateurs sont prêts à changer.</p>
<div class="tableau"><table>
<thead><tr><th>Outil</th><th>Utilisateurs 30 j</th><th>Prix</th><th>Signaux</th></tr></thead>
<tbody>{lignes_f}</tbody>
</table></div>

<h2>Nouveaux outils qui décollent</h2>
<p class="aide">Publiés récemment et déjà adoptés : signe d'un besoin émergent.</p>
<div class="tableau"><table>
<thead><tr><th>Outil</th><th>Utilisateurs 30 j</th><th>Âge</th><th>Utilisateurs / jour d'âge</th><th>Prix</th></tr></thead>
<tbody>{lignes_m}</tbody>
</table></div>

<footer>Source : API publique de l'Apify Store. Les chiffres d'utilisateurs mesurent l'usage, pas le chiffre d'affaires. Le score est un outil de tri : vérifiez toujours une niche à la main avant de construire.</footer>
</main>
</body>
</html>"""
    (DOSSIER_SORTIE / "index.html").write_text(page, encoding="utf-8")


# --------------------------------------------------------------------------
# Snapshots
# --------------------------------------------------------------------------

def charger_precedent() -> dict | None:
    fichiers = sorted(DOSSIER_SNAPSHOTS.glob("*.json.gz"))
    aujourd_hui = MAINTENANT.strftime("%Y-%m-%d")
    fichiers = [f for f in fichiers if not f.name.startswith(aujourd_hui)]
    if not fichiers:
        return None
    with gzip.open(fichiers[-1], "rt", encoding="utf-8") as f:
        return json.load(f)


def sauver_snapshot(outils, niches_calc):
    DOSSIER_SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    chemin = DOSSIER_SNAPSHOTS / f"{MAINTENANT.strftime('%Y-%m-%d')}.json.gz"
    contenu = {
        "date": MAINTENANT.isoformat(),
        "niches_demande": {n.requete: n.demande_30j for n in niches_calc},
        "outils": [asdict(o) for o in outils.values()],
    }
    with gzip.open(chemin, "wt", encoding="utf-8") as f:
        json.dump(contenu, f, ensure_ascii=False)
    return chemin


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Détecteur de demande — Apify Store")
    ap.add_argument("--demo", action="store_true", help="utiliser des données fictives")
    args = ap.parse_args()

    cfg = json.loads((RACINE / "config.json").read_text(encoding="utf-8"))

    if args.demo:
        outils, niches = donnees_demo(cfg)
        precedent = None
    else:
        outils, niches = collecter(cfg)
        precedent = charger_precedent()

    niches_calc, fragiles, montants = analyser(outils, niches, cfg, precedent)
    ecrire_csv(niches_calc, fragiles, montants)
    ecrire_html(niches_calc, fragiles, montants, len(outils), args.demo)
    if not args.demo:
        print(f"Snapshot : {sauver_snapshot(outils, niches_calc)}")

    print(f"\n{len(outils)} outils analysés, {len(niches_calc)} niches classées.")
    for i, n in enumerate(niches_calc[:10], 1):
        print(f"{i:>2}. {n.requete:<24} score {n.score:5.1f}  demande {n.demande_30j:>7}  actifs {n.nb_actifs:>3}")
    print(f"Rapport : {DOSSIER_SORTIE / 'index.html'}")


if __name__ == "__main__":
    main()
