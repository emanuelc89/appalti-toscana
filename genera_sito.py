"""
genera_sito.py - Genera le pagine statiche per comune, l'indice dei comuni e la sitemap.

Perche' HTML statico: il testo e' gia' nella pagina quando Google la scarica, quindi si indicizza
molto meglio di una pagina che si riempie con JavaScript.

Uso:
    python genera_sito.py                 # scrive dentro docs/
    python genera_sito.py --out _sito     # copia docs/ in _sito/ e genera li' (per un deploy da Actions)

Legge:   docs/dati/archivio/*.json e docs/dati/comuni.json
Scrive:  stile.css, comuni/index.html, comune/<slug>/index.html, sitemap.xml
         (solo i file il cui contenuto cambia: la cronologia git cresce poco)

Requisiti: Python 3.10+, nessuna dipendenza esterna.
"""
import argparse
import html
import json
import shutil
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

AQUI = Path(__file__).parent
DOCS = AQUI / "docs"
SITO = "https://emanuelc89.github.io/appalti-toscana"
# Token di Cloudflare Web Analytics (pannello Cloudflare > Web Analytics > Manage site > JS snippet).
# Lascia vuoto per non inserire il contatore.
CF_BEACON_TOKEN = ""
REPO = "https://github.com/emanuelc89/appalti-toscana"

MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
        "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]
TIPI_OPPORTUNITA = ("bando", "indagine_sotto_soglia", "indagine_sopra_soglia", "preinformazione")
TIPI_NOME = {
    "bando": "Bando di gara",
    "indagine_sotto_soglia": "Indagine di mercato sotto soglia",
    "indagine_sopra_soglia": "Indagine di mercato sopra soglia",
    "preinformazione": "Avviso di pre-informazione",
}
MIN_AVVISI_INDICIZZAZIONE = 3   # sotto questa soglia la pagina esiste ma Google non la indicizza
MAX_APERTE = 30
MAX_AFFIDAMENTI = 40
MAX_ESITI = 20
MAX_FORNITORI = 10
MAX_ENTI = 8


# ---------- formattazione ----------

def esc(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def euro(v) -> str:
    return f"{round(v):,}".replace(",", ".") + "\u00a0€"


def numero(n) -> str:
    return f"{n:,}".replace(",", ".")


def data_it(iso: str, breve: bool = False) -> str:
    a, m, g = (int(x) for x in iso.split("-"))
    mese = MESI[m - 1]
    return f"{g} {mese[:3]} {a}" if breve else f"{g} {mese} {a}"


def plur(n, singolare, plurale):
    return f"{numero(n)} {singolare if n == 1 else plurale}"


def scade(iso: str) -> str:
    """'Scade l'8 ottobre 2026' / 'Scade il 3 ottobre 2026' (elisione solo per 8 e 11)."""
    giorno = int(iso.split("-")[2])
    return f"Scade {'l' + chr(39) if giorno in (8, 11) else 'il '}{data_it(iso)}"


def url_ok(u):
    return u if isinstance(u, str) and u.startswith(("http://", "https://")) else None


# ---------- dati ----------

def chiave(r) -> str:
    return f"{r['template']}:{r['procedura'] or r['id']}"


def carica_record(dati: Path):
    """Tutti i record dell'archivio, una sola versione (la piu' recente) per procedura."""
    mappa = {}
    for f in sorted((dati / "archivio").glob("*.json")):
        for r in json.loads(f.read_text(encoding="utf-8")):
            k = chiave(r)
            if k not in mappa or r["pubblicato"] >= mappa[k]["pubblicato"]:
                mappa[k] = r
    return list(mappa.values())


def fornitori(recs):
    """Classifica degli aggiudicatari: numero di avvisi e importo dei soli contratti a operatore singolo."""
    tab = {}
    for r in recs:
        if r["tipo"] not in ("affidamento_diretto", "esito") or not r["aggiudicatari"]:
            continue
        visti = set()
        for a in r["aggiudicatari"]:
            nome = a.get("denominazione")
            if not nome:
                continue
            k = nome.strip().upper()
            e = tab.setdefault(k, {"nome": nome.strip(), "n": 0, "importo": 0.0})
            if k not in visti:
                e["n"] += 1
                visti.add(k)
            if len(r["aggiudicatari"]) == 1 and isinstance(a.get("importo"), (int, float)):
                e["importo"] += a["importo"]
    return sorted(tab.values(), key=lambda e: (-e["n"], -e["importo"], e["nome"]))[:MAX_FORNITORI]


def riepilogo(recs, oggi_iso, inizio_iso):
    ad = sorted((r for r in recs if r["tipo"] == "affidamento_diretto"),
                key=lambda r: (r["pubblicato"], r["id"]), reverse=True)
    es = sorted((r for r in recs if r["tipo"] == "esito"),
                key=lambda r: (r["pubblicato"], r["id"]), reverse=True)
    aperte = sorted((r for r in recs if r["tipo"] in TIPI_OPPORTUNITA and r["scadenza"]
                     and not r["anomalia_scadenza"] and r["scadenza"] >= oggi_iso),
                    key=lambda r: (r["scadenza"], r["id"]))
    periodo = [r for r in recs if r["pubblicato"] >= inizio_iso]
    ad_p = [r for r in ad if r["pubblicato"] >= inizio_iso]
    es_p = [r for r in es if r["pubblicato"] >= inizio_iso]
    totale = sum(r["valore"] for r in ad_p if isinstance(r["valore"], (int, float)))
    enti = Counter(r["committente"] for r in periodo
                   if r["tipo"] in ("affidamento_diretto", "esito") and r["committente"])
    return {
        "ad": ad, "es": es, "aperte": aperte,
        "n_ad": len(ad_p), "n_es": len(es_p), "totale_ad": totale,
        "fornitori": fornitori(periodo),
        "enti": enti.most_common(MAX_ENTI),
        "ultimo": max((r["pubblicato"] for r in recs), default=None),
    }


# ---------- pagine ----------

TESTA = """<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titolo}</title>
<meta name="description" content="{descrizione}">
<link rel="canonical" href="{canonical}">
{robots}<meta property="og:type" content="website">
<meta property="og:title" content="{titolo}">
<meta property="og:description" content="{descrizione}">
<meta property="og:url" content="{canonical}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&family=Barlow+Condensed:wght@500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{base}stile.css">
{extra_head}</head>
<body>
"""

PIEDE = """{analytics}<footer>
  <div class="contenitore">
    <p>Fonte: ANAC, <a href="https://pubblicitalegale.anticorruzione.it" rel="noopener">Piattaforma di Pubblicità a Valore Legale</a>. Fa fede solo la pubblicazione ufficiale: controlla sempre l'avviso originale.</p>
    <p>Il comune è ricostruito dal luogo di esecuzione indicato nell'avviso, non dalla sede dell'ente. Dei vincitori si pubblicano solo le società: persone fisiche e ditte individuali non sono riportate. Progetto indipendente, non affiliato ad ANAC né alle stazioni appaltanti.</p>
    <p>Codice su <a href="{repo}" rel="noopener">GitHub</a> con licenza AGPL-3.0. Dati con licenza <a href="https://creativecommons.org/licenses/by-sa/4.0/deed.it" rel="noopener license">CC BY-SA 4.0</a>: puoi riusarli citando Appalti Toscana e ANAC come fonte.</p>
  </div>
</footer>
</body>
</html>
"""


def snippet_analytics() -> str:
    if not CF_BEACON_TOKEN:
        return ""
    return ('<script defer src="https://static.cloudflareinsights.com/beacon.min.js" '
            f'data-cf-beacon=\'{{"token": "{CF_BEACON_TOKEN}"}}\'></script>\n')


def intestazione(base, briciole):
    voci = "".join(
        f'<li><a href="{esc(u)}">{esc(t)}</a></li>' if u else f'<li aria-current="page">{esc(t)}</li>'
        for t, u in briciole)
    return f"""<nav class="briciole" aria-label="Percorso"><div class="contenitore"><ol>{voci}</ol></div></nav>
"""


def json_ld_briciole(briciole):
    elementi = []
    for i, (t, u) in enumerate(briciole, 1):
        voce = {"@type": "ListItem", "position": i, "name": t}
        if u:
            voce["item"] = u
        elementi.append(voce)
    dati = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": elementi}
    return '<script type="application/ld+json">' + json.dumps(dati, ensure_ascii=False).replace("</", "<\\/") + "</script>\n"


def riga_avviso(r, tipo_riga):
    """Una riga d'elenco: tipo_riga = 'aperta' | 'affidamento' | 'esito'."""
    dettagli = []
    if tipo_riga == "aperta":
        dettagli.append(f"<li>{esc(TIPI_NOME.get(r['tipo'], r['tipo']))}</li>")
    if isinstance(r["valore"], (int, float)):
        dettagli.append(f'<li class="valore">{euro(r["valore"])}</li>')
    if tipo_riga == "aperta":
        dettagli.append(f"<li>{esc(scade(r['scadenza']))}</li>")
    if tipo_riga == "esito" and r.get("ribasso_pct") is not None:
        dettagli.append(f"<li>Ribasso indicativo {str(r['ribasso_pct']).replace('.', ',')}%</li>")
    if tipo_riga in ("affidamento", "esito"):
        nomi = [a["denominazione"] for a in r["aggiudicatari"] if a.get("denominazione")]
        if nomi:
            visibili = "; ".join(esc(n) for n in nomi[:3])
            if len(nomi) > 3:
                visibili += f" e altri {len(nomi) - 3}"
            dettagli.append(f"<li>Aggiudicatario: {visibili}</li>")
        elif r["aggiudicatari_nascosti"]:
            dettagli.append("<li>Aggiudicatario: non pubblicato (persona fisica o ditta individuale)</li>")
    quando = data_it(r["scadenza"], True) if tipo_riga == "aperta" else data_it(r["pubblicato"], True)
    etichetta = "scade" if tipo_riga == "aperta" else "pubblicato"
    breve = esc((r["titolo"] or "")[:80])
    link = url_ok(r.get("url_anac"))
    doc = url_ok(r.get("link_documenti")) if tipo_riga == "aperta" else None
    collegamenti = ""
    if link:
        collegamenti += f'<a class="principale" href="{esc(link)}" target="_blank" rel="noopener">Avviso ufficiale<span class="vh">: {breve}</span></a>'
    if doc:
        collegamenti += f'<a href="{esc(doc)}" target="_blank" rel="noopener">Documenti di gara<span class="vh">: {breve}</span></a>'
    return f"""<li class="avviso">
<div class="quando"><span class="et">{etichetta}</span>{esc(quando)}</div>
<div><h3 class="oggetto">{esc(r['titolo'] or 'Oggetto non indicato')}</h3>
<p class="ente">{esc(r['committente'] or 'Ente non indicato')}</p>
<ul class="dettagli">{''.join(dettagli)}</ul></div>
<div class="collegamenti">{collegamenti}</div>
</li>
"""


def pagina_comune(c, rs, ctx):
    nome, prov = c["nome"], c["provincia"]
    base = "../../"
    n_tot = len(rs["ad"]) + len(rs["es"]) + len(rs["aperte"])
    inizio = data_it(ctx["inizio"])
    titolo = f"Appalti e gare a {nome} ({prov}): affidamenti diretti, bandi ed esiti"
    if rs["n_ad"] or rs["n_es"] or rs["aperte"]:
        parti = []
        if rs["n_ad"]:
            parti.append(f"{plur(rs['n_ad'], 'affidamento diretto', 'affidamenti diretti')} per {euro(rs['totale_ad'])}")
        if rs["n_es"]:
            parti.append(plur(rs['n_es'], 'esito di gara', 'esiti di gara'))
        if rs["aperte"]:
            parti.append(plur(len(rs['aperte']), 'gara aperta', 'gare aperte'))
        descrizione = (f"Appalti pubblici a {nome} ({prov}) dal {inizio}: " + ", ".join(parti)
                       + ". Chi vince, quanto e quando. Dati ANAC aggiornati ogni giorno.")
    else:
        descrizione = f"Appalti pubblici a {nome} ({prov}): nessun avviso trovato nel periodo coperto. Dati ANAC."
    briciole = [("Appalti Toscana", f"{SITO}/"), ("Comuni", f"{SITO}/comuni/"),
                (prov, f"{SITO}/comuni/#{prov.lower().replace(' ', '-')}"), (nome, None)]
    canonical = f"{SITO}/comune/{c['slug']}/"
    vuota = n_tot == 0
    noindex = n_tot < MIN_AVVISI_INDICIZZAZIONE
    out = TESTA.format(titolo=esc(titolo), descrizione=esc(descrizione), canonical=esc(canonical), base=base,
                       robots='<meta name="robots" content="noindex">\n' if noindex else "",
                       extra_head=json_ld_briciole(briciole))
    out += intestazione(base, [("Appalti Toscana", base), ("Comuni", base + "comuni/"), (nome, None)])
    out += f"""<header class="testata"><div class="contenitore">
<h1>Appalti e gare a {esc(nome)}</h1>
<p class="sottotitolo">Affidamenti diretti, bandi ed esiti di gara con luogo di esecuzione a {esc(nome)}, provincia di {esc(prov)}, dalla pubblicità legale di ANAC.</p>
<p class="aggiornato">Dati dal {esc(inizio)}{(' al ' + esc(data_it(rs['ultimo']))) if rs['ultimo'] else ''}</p>
</div></header>
<main class="contenitore">
"""
    if vuota:
        out += f'<p class="vuoto">Non risultano avvisi con luogo di esecuzione a {esc(nome)} nel periodo coperto dai dati. <a href="{base}">Vedi le gare aperte in tutta la Toscana</a>.</p>\n'
    else:
        out += f"""<dl class="numeri">
<div><dt>{'Affidamento diretto' if rs['n_ad'] == 1 else 'Affidamenti diretti'}</dt><dd>{numero(rs['n_ad'])}</dd></div>
<div><dt>Importo affidato</dt><dd>{euro(rs['totale_ad'])}</dd></div>
<div><dt>{'Esito di gara' if rs['n_es'] == 1 else 'Esiti di gara'}</dt><dd>{numero(rs['n_es'])}</dd></div>
<div><dt>{'Gara aperta ora' if len(rs['aperte']) == 1 else 'Gare aperte ora'}</dt><dd>{numero(len(rs['aperte']))}</dd></div>
</dl>
<p class="nota">Numeri dal {esc(inizio)}. L'importo somma gli affidamenti diretti sotto soglia pubblicati per questo comune.</p>
"""
        if rs["aperte"]:
            out += f'<section aria-labelledby="aperte"><h2 id="aperte">Gare e avvisi aperti a {esc(nome)}</h2>\n<ul class="elenco">\n'
            out += "".join(riga_avviso(r, "aperta") for r in rs["aperte"][:MAX_APERTE])
            out += "</ul>\n"
            if len(rs["aperte"]) > MAX_APERTE:
                out += f'<p class="nota">Mostrate le prime {MAX_APERTE} di {len(rs["aperte"])}, per scadenza.</p>\n'
            out += "</section>\n"
        if rs["fornitori"]:
            out += f'<section aria-labelledby="fornitori"><h2 id="fornitori">Chi riceve più incarichi a {esc(nome)}</h2>\n'
            out += '<p class="aiuto">Operatori economici con più avvisi di aggiudicazione dal ' + esc(inizio) + '. L\'importo conta solo i contratti con un unico aggiudicatario.</p>\n<ol class="classifica">\n'
            for e in rs["fornitori"]:
                imp = f' · {euro(e["importo"])}' if e["importo"] else ""
                out += f'<li><span class="nome">{esc(e["nome"])}</span><span class="cifre">{numero(e["n"])} {"avviso" if e["n"] == 1 else "avvisi"}{imp}</span></li>\n'
            out += "</ol></section>\n"
        if rs["ad"]:
            out += f'<section aria-labelledby="affidamenti"><h2 id="affidamenti">Ultimi affidamenti diretti a {esc(nome)}</h2>\n<ul class="elenco">\n'
            out += "".join(riga_avviso(r, "affidamento") for r in rs["ad"][:MAX_AFFIDAMENTI])
            out += "</ul>\n"
            if len(rs["ad"]) > MAX_AFFIDAMENTI:
                out += f'<p class="nota">Mostrati gli ultimi {MAX_AFFIDAMENTI} di {numero(len(rs["ad"]))}.</p>\n'
            out += "</section>\n"
        if rs["es"]:
            out += f'<section aria-labelledby="esiti"><h2 id="esiti">Ultimi esiti di gara a {esc(nome)}</h2>\n<ul class="elenco">\n'
            out += "".join(riga_avviso(r, "esito") for r in rs["es"][:MAX_ESITI])
            out += "</ul>\n"
            if len(rs["es"]) > MAX_ESITI:
                out += f'<p class="nota">Mostrati gli ultimi {MAX_ESITI} di {numero(len(rs["es"]))}.</p>\n'
            out += "</section>\n"
        if rs["enti"]:
            out += f'<section aria-labelledby="enti"><h2 id="enti">Quali enti affidano a {esc(nome)}</h2>\n<ol class="classifica">\n'
            for ente, n in rs["enti"]:
                out += f'<li><span class="nome">{esc(ente)}</span><span class="cifre">{numero(n)} {"avviso" if n == 1 else "avvisi"}</span></li>\n'
            out += "</ol></section>\n"
    out += "</main>\n" + PIEDE.format(repo=REPO, analytics=snippet_analytics())
    return out


def pagina_indice(per_provincia, conteggi, ctx):
    base = "../"
    titolo = "Appalti pubblici in Toscana: tutti i comuni"
    descrizione = ("Affidamenti diretti, bandi ed esiti di gara per ciascuno dei 273 comuni toscani, "
                   "dalla pubblicità legale di ANAC. Scegli il tuo comune.")
    briciole = [("Appalti Toscana", f"{SITO}/"), ("Comuni", None)]
    out = TESTA.format(titolo=esc(titolo), descrizione=esc(descrizione), canonical=f"{SITO}/comuni/", base=base,
                       robots="", extra_head=json_ld_briciole(briciole))
    out += intestazione(base, [("Appalti Toscana", base), ("Comuni", None)])
    out += """<header class="testata"><div class="contenitore">
<h1>Appalti pubblici per comune</h1>
<p class="sottotitolo">Scegli il tuo comune per vedere chi vince gli appalti, quanto viene affidato e quali gare sono aperte.</p>
</div></header>
<main class="contenitore">
"""
    for prov in sorted(per_provincia, key=lambda p: p.lower()):
        ancora = prov.lower().replace(" ", "-")
        out += f'<section aria-labelledby="{esc(ancora)}"><h2 id="{esc(ancora)}">Provincia di {esc(prov)}</h2>\n<ul class="indice-comuni">\n'
        for c in sorted(per_provincia[prov], key=lambda x: x["nome"]):
            n = conteggi.get(c["slug"], 0)
            sub = f'<span class="cifre">{plur(n, "avviso", "avvisi")}</span>' if n else '<span class="cifre">nessun avviso</span>'
            out += f'<li><a href="{base}comune/{esc(c["slug"])}/">{esc(c["nome"])}</a> {sub}</li>\n'
        out += "</ul></section>\n"
    out += "</main>\n" + PIEDE.format(repo=REPO, analytics=snippet_analytics())
    return out


def sitemap(voci):
    righe = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lastmod in voci:
        righe.append(f"<url><loc>{esc(loc)}</loc><lastmod>{lastmod}</lastmod></url>")
    righe.append("</urlset>")
    return "\n".join(righe) + "\n"


# ---------- stile ----------

STILE = """/* Stile condiviso delle pagine per comune: stessi toni del sito principale */
:root { --carta:#FBFBF9; --inchiostro:#1D232B; --timbro:#3D44A8; --urgente:#B42318; --filo:#D9DCE1; --tenue:#5F6670; --fondo:#EEF0F4;
  --testo:"Atkinson Hyperlegible", system-ui, -apple-system, "Segoe UI", sans-serif; --display:"Barlow Condensed", "Arial Narrow", system-ui, sans-serif; }
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { margin:0; background:var(--carta); color:var(--inchiostro); font:400 1.0625rem/1.55 var(--testo); }
.contenitore { max-width:68rem; margin:0 auto; padding:0 1.25rem; }
.vh { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; }
.briciole { background:var(--fondo); font-size:.9375rem; padding:.55rem 0; }
.briciole ol { list-style:none; margin:0; padding:0; display:flex; flex-wrap:wrap; gap:.25rem .6rem; }
.briciole li + li::before { content:"›"; color:var(--tenue); margin-right:.6rem; }
.briciole a { color:var(--timbro); }
.testata { background:#fff; border-bottom:3px solid var(--inchiostro); padding:2rem 0 1.25rem; }
h1 { font:600 clamp(2.2rem,6vw,3.4rem)/1.02 var(--display); letter-spacing:-.01em; margin:0 0 .6rem; }
.sottotitolo { margin:0; max-width:42em; font-size:1.125rem; }
.aggiornato { margin:.75rem 0 0; color:var(--tenue); font-size:.9375rem; }
h2 { font:600 1.6rem/1.15 var(--display); margin:2.25rem 0 .4rem; }
.aiuto, .nota { margin:.25rem 0 .75rem; color:var(--tenue); font-size:.9375rem; max-width:62ch; }
.numeri { display:grid; grid-template-columns:repeat(4,1fr); gap:1px; background:var(--filo); border:1px solid var(--filo); margin:1.75rem 0 .5rem; }
.numeri div { background:#fff; padding:1rem 1.1rem; }
.numeri dt { color:var(--tenue); font-size:.9375rem; }
.numeri dd { margin:.15rem 0 0; font:600 clamp(1.7rem,4vw,2.4rem)/1.05 var(--display); }
.elenco { list-style:none; margin:0; padding:0; border-top:1px solid var(--filo); }
.avviso { display:grid; grid-template-columns:6.5rem 1fr auto; gap:1.25rem; padding:1.1rem 0; border-bottom:1px solid var(--filo); }
.quando { font:600 1.15rem/1.15 var(--display); }
.quando .et { display:block; font:500 .9rem/1.2 var(--display); color:var(--tenue); text-transform:uppercase; letter-spacing:.04em; }
.oggetto { font:700 1.0625rem/1.4 var(--testo); margin:0 0 .3rem; max-width:62ch; overflow-wrap:anywhere; }
.ente { margin:0 0 .35rem; }
.dettagli { display:flex; flex-wrap:wrap; gap:.2rem 1.25rem; list-style:none; margin:0; padding:0; color:var(--tenue); font-size:.9375rem; }
.dettagli .valore { color:var(--inchiostro); font-weight:700; }
.collegamenti { display:flex; flex-direction:column; align-items:flex-start; gap:.5rem; min-width:11rem; }
.collegamenti a { color:var(--timbro); font-weight:700; text-underline-offset:.2em; }
.collegamenti a.principale { background:var(--timbro); color:#fff; padding:.55rem .9rem; border-radius:3px; text-decoration:none; }
.collegamenti a.principale:hover { background:var(--inchiostro); }
.classifica { list-style:none; margin:0; padding:0; border-top:1px solid var(--filo); counter-reset:pos; }
.classifica li { display:flex; justify-content:space-between; gap:1rem; padding:.7rem 0; border-bottom:1px solid var(--filo); counter-increment:pos; }
.classifica li::before { content:counter(pos); font:600 1.2rem/1.3 var(--display); color:var(--tenue); width:1.6rem; flex:none; }
.classifica .nome { flex:1; font-weight:700; overflow-wrap:anywhere; }
.classifica .cifre, .indice-comuni .cifre { color:var(--tenue); font-size:.9375rem; white-space:nowrap; }
.indice-comuni { list-style:none; margin:0; padding:0; columns:3 16rem; column-gap:2rem; }
.indice-comuni li { break-inside:avoid; padding:.3rem 0; display:flex; justify-content:space-between; gap:.75rem; border-bottom:1px solid var(--filo); }
.indice-comuni a { color:var(--timbro); font-weight:700; text-underline-offset:.2em; }
.vuoto { padding:2rem 0; color:var(--tenue); }
footer { margin-top:3rem; background:#fff; border-top:3px solid var(--inchiostro); padding:1.5rem 0 3rem; font-size:.9375rem; }
footer p { max-width:62ch; margin:.5rem 0; }
footer a { color:var(--timbro); }
a:focus-visible { outline:3px solid var(--timbro); outline-offset:2px; }
@media (max-width:720px) {
  .numeri { grid-template-columns:1fr 1fr; }
  .avviso { grid-template-columns:4.8rem 1fr; gap:1rem; }
  .collegamenti { grid-column:2; flex-direction:row; flex-wrap:wrap; min-width:0; }
}
"""


# ---------- scrittura ----------

class Scrittore:
    def __init__(self):
        self.nuovi = self.cambiati = self.invariati = 0

    def scrivi(self, percorso: Path, testo: str):
        percorso.parent.mkdir(parents=True, exist_ok=True)
        if percorso.exists():
            if percorso.read_text(encoding="utf-8") == testo:
                self.invariati += 1
                return
            self.cambiati += 1
        else:
            self.nuovi += 1
        percorso.write_text(testo, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="cartella di destinazione (default: docs)")
    args = ap.parse_args()

    out = Path(args.out) if args.out else DOCS
    if out.resolve() != DOCS.resolve():
        shutil.copytree(DOCS, out, dirs_exist_ok=True)

    dati = DOCS / "dati"
    comuni = json.loads((dati / "comuni.json").read_text(encoding="utf-8"))
    per_slug = {c["slug"]: c for c in comuni}
    records = carica_record(dati)
    if not records:
        raise SystemExit("Nessun record nell'archivio: esegui prima aggiorna.py")

    oggi = date.today()
    minimo = min(r["pubblicato"] for r in records)
    inizio_iso = max((oggi - timedelta(days=365)).isoformat(), minimo)
    ctx = {"inizio": inizio_iso}

    per_comune = defaultdict(list)
    orfani = 0
    for r in records:
        if r["geo"] not in ("A", "B", "C"):
            continue
        for s in set(r["comuni_slug"]):
            if s in per_slug:
                per_comune[s].append(r)
            else:
                orfani += 1

    w = Scrittore()
    w.scrivi(out / "stile.css", STILE)
    conteggi = {}
    voci = [(f"{SITO}/", oggi.isoformat()), (f"{SITO}/comuni/", oggi.isoformat())]
    senza_dati = 0
    for c in comuni:
        recs = per_comune.get(c["slug"], [])
        rs = riepilogo(recs, oggi.isoformat(), inizio_iso)
        conteggi[c["slug"]] = len(recs)
        w.scrivi(out / "comune" / c["slug"] / "index.html", pagina_comune(c, rs, ctx))
        n_visibili = len(rs["ad"]) + len(rs["es"]) + len(rs["aperte"])
        if n_visibili >= MIN_AVVISI_INDICIZZAZIONE:
            voci.append((f"{SITO}/comune/{c['slug']}/", rs["ultimo"]))
        else:
            senza_dati += 1

    per_provincia = defaultdict(list)
    for c in comuni:
        per_provincia[c["provincia"]].append(c)
    w.scrivi(out / "comuni" / "index.html", pagina_indice(per_provincia, conteggi, ctx))
    w.scrivi(out / "sitemap.xml", sitemap(voci))

    print(f"Record letti: {len(records)} | dati dal {minimo} | periodo delle statistiche dal {inizio_iso}")
    print(f"Pagine comune: {len(comuni)} ({len(comuni) - senza_dati} indicizzabili, {senza_dati} con meno di "
          f"{MIN_AVVISI_INDICIZZAZIONE} avvisi: noindex e fuori dalla sitemap)")
    print(f"Sitemap: {len(voci)} indirizzi | riferimenti a comuni non in elenco: {orfani}")
    print(f"File scritti: {w.nuovi} nuovi, {w.cambiati} modificati, {w.invariati} invariati | destinazione: {out}")


if __name__ == "__main__":
    main()
