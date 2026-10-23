"""
genera_sito.py - Genera le pagine statiche per comune, l'indice dei comuni e la sitemap.

Perche' HTML statico: il testo e' gia' nella pagina quando Google la scarica, quindi si indicizza
molto meglio di una pagina che si riempie con JavaScript.

Uso:
    python genera_sito.py                 # scrive dentro docs/
    python genera_sito.py --out _sito     # copia docs/ in _sito/ e genera li' (per un deploy da Actions)

Legge:   docs/dati/archivio/*.json e docs/dati/comuni.json
Scrive:  stile.css, tema.js, comuni/index.html, comune/<slug>/index.html, sitemap.xml
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
CF_BEACON_TOKEN = "e7b35b91883449e0b1b69fbca05fce21"
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


def euro_breve(v) -> str:
    """Importo compatto per le schede numeriche, con l'unita' in piccolo: 272,8 <small>mln €</small>."""
    if v >= 1e9:
        return f"{v / 1e9:.1f}".replace(".", ",") + "<small>\u00a0mld\u00a0€</small>"
    if v >= 1e6:
        return f"{v / 1e6:.1f}".replace(".", ",") + "<small>\u00a0mln\u00a0€</small>"
    return f"{round(v):,}".replace(",", ".") + "<small>\u00a0€</small>"


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
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{titolo}</title>
<meta name="description" content="{descrizione}">
<link rel="canonical" href="{canonical}">
{robots}<meta property="og:type" content="website">
<meta property="og:title" content="{titolo}">
<meta property="og:description" content="{descrizione}">
<meta property="og:url" content="{canonical}">
<meta name="theme-color" content="#F4F5F8" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#11141B" media="(prefers-color-scheme: dark)">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%233D44A8'/%3E%3Cpath d='M9 10h14M9 16h14M9 22h9' stroke='white' stroke-width='2.6' stroke-linecap='round'/%3E%3C/svg%3E">
<script>try{{var t=localStorage.getItem("tema");if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t)}}catch(e){{}}</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&family=Barlow+Condensed:wght@500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{base}stile.css">
<script src="{base}tema.js" defer></script>
{extra_head}</head>
<body>
<a class="salta" href="#contenuto">Vai al contenuto</a>
"""

PIEDE = """{analytics}<footer class="piede">
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


def barra(base, attiva):
    """Barra superiore comune a tutte le pagine: marchio, sezioni e tema."""
    voci = [("Gare aperte", base, "gare"), ("Comuni", base + "comuni/", "comuni")]
    link = "".join(
        f'<a href="{esc(u)}"{" aria-current=" + chr(34) + "page" + chr(34) if k == attiva else ""}>{esc(t)}</a>'
        for t, u, k in voci)
    return f"""<header class="barra"><div class="contenitore">
<a class="marchio" href="{esc(base)}"><span class="marchio-icona" aria-hidden="true"></span>Appalti Toscana</a>
<nav class="navigazione" aria-label="Sezioni del sito">{link}</nav>
<button class="btn btn-icona" id="bottone-tema" type="button" aria-label="Cambia tema"></button>
</div></header>
"""


def intestazione(base, briciole, attiva="comuni"):
    voci = "".join(
        f'<li><a href="{esc(u)}">{esc(t)}</a></li>' if u else f'<li aria-current="page">{esc(t)}</li>'
        for t, u in briciole)
    return barra(base, attiva) + f"""<nav class="briciole" aria-label="Percorso"><div class="contenitore"><ol>{voci}</ol></div></nav>
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


ICONA_ESTERNO = ('<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" '
                 'stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>')


def riga_avviso(r, tipo_riga):
    """Una riga d'elenco: tipo_riga = 'aperta' | 'affidamento' | 'esito'."""
    dettagli = []
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
            dettagli.append(f'<li class="vincitore">Aggiudicatario: <strong>{visibili}</strong></li>')
        elif r["aggiudicatari_nascosti"]:
            dettagli.append("<li>Aggiudicatario: non pubblicato (persona fisica o ditta individuale)</li>")
    cig = [c for c in (r.get("cig") or []) if c]
    if cig:
        dettagli.append(f'<li class="cig">CIG {esc(cig[0])}{" +" + str(len(cig) - 1) if len(cig) > 1 else ""}</li>')
    quando = data_it(r["scadenza"], True) if tipo_riga == "aperta" else data_it(r["pubblicato"], True)
    etichetta = "scade" if tipo_riga == "aperta" else "pubblicato"
    breve = esc((r["titolo"] or "")[:80])
    link = url_ok(r.get("url_anac"))
    doc = url_ok(r.get("link_documenti")) if tipo_riga == "aperta" else None
    collegamenti = ""
    if link:
        collegamenti += f'<a class="btn btn-primario" href="{esc(link)}" target="_blank" rel="noopener">Avviso ufficiale{ICONA_ESTERNO}<span class="vh">: {breve}</span></a>'
    if doc:
        collegamenti += f'<a class="btn" href="{esc(doc)}" target="_blank" rel="noopener">Documenti di gara{ICONA_ESTERNO}<span class="vh">: {breve}</span></a>'
    tipo = (f'<span class="etichetta-tipo">{esc(TIPI_NOME.get(r["tipo"], r["tipo"]))}</span>'
            if tipo_riga == "aperta" else "")
    data_iso = r["scadenza"] if tipo_riga == "aperta" else r["pubblicato"]
    return f"""<li class="avviso">
<div class="quando"><span class="et">{etichetta}</span><time datetime="{esc(data_iso)}">{esc(quando)}</time></div>
<div class="corpo">{tipo}<h3 class="oggetto">{esc(r['titolo'] or 'Oggetto non indicato')}</h3>
<p class="ente">{esc(r['committente'] or 'Ente non indicato')}</p>
<ul class="dettagli">{''.join(dettagli)}</ul></div>
<div class="collegamenti">{collegamenti}</div>
</li>
"""


def classifica(voci, nome_cifra):
    """Classifica con barra proporzionale. voci = [(nome, n, testo_extra)]."""
    if not voci:
        return ""
    massimo = max(n for _, n, _ in voci) or 1
    righe = "".join(
        f'<li><span class="nome">{esc(nome)}</span><span class="cifre">{numero(n)} {nome_cifra(n)}{extra}</span>'
        f'<span class="barra-rel" aria-hidden="true"><i style="width:{max(4, round(n / massimo * 100))}%"></i></span></li>\n'
        for nome, n, extra in voci)
    return f'<ol class="classifica">\n{righe}</ol>'


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
    out += intestazione(base, [("Appalti Toscana", base), ("Comuni", base + "comuni/"),
                               (prov, base + "comuni/#" + prov.lower().replace(" ", "-")), (nome, None)])
    out += f"""<header class="testata"><div class="contenitore">
<p class="occhiello">Comune · provincia di {esc(prov)}</p>
<h1>Appalti e gare a {esc(nome)}</h1>
<p class="sottotitolo">Affidamenti diretti, bandi ed esiti di gara con luogo di esecuzione a {esc(nome)}, dalla pubblicità legale di ANAC.</p>
<p class="aggiornato">Dati dal {esc(inizio)}{(' al ' + esc(data_it(rs['ultimo']))) if rs['ultimo'] else ''}</p>
</div></header>
<main class="contenitore" id="contenuto" tabindex="-1">
"""
    if vuota:
        out += (f'<div class="vuoto"><p><strong>Nessun avviso trovato.</strong> Non risultano avvisi con luogo di esecuzione '
                f'a {esc(nome)} nel periodo coperto dai dati.</p><p class="azioni">'
                f'<a class="btn btn-primario" href="{base}">Vedi le gare aperte in Toscana</a>'
                f'<a class="btn" href="{base}comuni/">Scegli un altro comune</a></p></div>\n')
    else:
        out += f"""<dl class="numeri">
<div><dt>{'Affidamento diretto' if rs['n_ad'] == 1 else 'Affidamenti diretti'}</dt><dd>{numero(rs['n_ad'])}</dd></div>
<div><dt>Importo affidato</dt><dd title="{euro(rs['totale_ad'])}">{euro_breve(rs['totale_ad'])}</dd></div>
<div><dt>{'Esito di gara' if rs['n_es'] == 1 else 'Esiti di gara'}</dt><dd>{numero(rs['n_es'])}</dd></div>
<div class="evidenza"><dt>{'Gara aperta ora' if len(rs['aperte']) == 1 else 'Gare aperte ora'}</dt><dd>{numero(len(rs['aperte']))}</dd></div>
</dl>
<p class="nota">Numeri dal {esc(inizio)}. L'importo somma gli affidamenti diretti sotto soglia pubblicati per questo comune.</p>
"""
        # Indice delle sezioni presenti, per saltare subito a quella che interessa.
        sezioni = []
        if rs["aperte"]:
            sezioni.append(("aperte", "Gare aperte", len(rs["aperte"])))
        if rs["fornitori"]:
            sezioni.append(("fornitori", "Chi vince", len(rs["fornitori"])))
        if rs["ad"]:
            sezioni.append(("affidamenti", "Affidamenti diretti", rs["n_ad"] or len(rs["ad"])))
        if rs["es"]:
            sezioni.append(("esiti", "Esiti di gara", rs["n_es"] or len(rs["es"])))
        if rs["enti"]:
            sezioni.append(("enti", "Enti", len(rs["enti"])))
        if len(sezioni) > 1:
            out += ('<nav class="indice-sezioni" aria-label="Sezioni della pagina">'
                    + "".join(f'<a href="#{k}">{esc(t)} <span class="conta">{numero(n)}</span></a>' for k, t, n in sezioni)
                    + "</nav>\n")
        if rs["aperte"]:
            out += f'<section class="blocco" aria-labelledby="aperte"><h2 id="aperte">Gare e avvisi aperti a {esc(nome)}</h2>\n<ul class="elenco">\n'
            out += "".join(riga_avviso(r, "aperta") for r in rs["aperte"][:MAX_APERTE])
            out += "</ul>\n"
            if len(rs["aperte"]) > MAX_APERTE:
                out += f'<p class="nota">Mostrate le prime {MAX_APERTE} di {len(rs["aperte"])}, per scadenza.</p>\n'
            out += "</section>\n"
        if rs["fornitori"]:
            out += f'<section class="blocco" aria-labelledby="fornitori"><h2 id="fornitori">Chi riceve più incarichi a {esc(nome)}</h2>\n'
            out += '<p class="aiuto">Operatori economici con più avvisi di aggiudicazione dal ' + esc(inizio) + '. L\'importo conta solo i contratti con un unico aggiudicatario.</p>\n'
            out += classifica([(e["nome"], e["n"], f' · {euro(e["importo"])}' if e["importo"] else "") for e in rs["fornitori"]],
                              lambda n: "avviso" if n == 1 else "avvisi")
            out += "</section>\n"
        if rs["ad"]:
            out += f'<section class="blocco" aria-labelledby="affidamenti"><h2 id="affidamenti">Ultimi affidamenti diretti a {esc(nome)}</h2>\n<ul class="elenco">\n'
            out += "".join(riga_avviso(r, "affidamento") for r in rs["ad"][:MAX_AFFIDAMENTI])
            out += "</ul>\n"
            if len(rs["ad"]) > MAX_AFFIDAMENTI:
                out += f'<p class="nota">Mostrati gli ultimi {MAX_AFFIDAMENTI} di {numero(len(rs["ad"]))}.</p>\n'
            out += "</section>\n"
        if rs["es"]:
            out += f'<section class="blocco" aria-labelledby="esiti"><h2 id="esiti">Ultimi esiti di gara a {esc(nome)}</h2>\n<ul class="elenco">\n'
            out += "".join(riga_avviso(r, "esito") for r in rs["es"][:MAX_ESITI])
            out += "</ul>\n"
            if len(rs["es"]) > MAX_ESITI:
                out += f'<p class="nota">Mostrati gli ultimi {MAX_ESITI} di {numero(len(rs["es"]))}.</p>\n'
            out += "</section>\n"
        if rs["enti"]:
            out += f'<section class="blocco" aria-labelledby="enti"><h2 id="enti">Quali enti affidano a {esc(nome)}</h2>\n'
            out += classifica([(ente, n, "") for ente, n in rs["enti"]], lambda n: "avviso" if n == 1 else "avvisi")
            out += "</section>\n"
        out += f'<p class="torna"><a class="btn" href="{base}comuni/">← Tutti i comuni</a> <a class="btn" href="#contenuto">↑ Torna su</a></p>\n'
    out += "</main>\n" + PIEDE.format(repo=REPO, analytics=snippet_analytics())
    return out


# Filtro istantaneo dell'elenco dei comuni (la pagina resta navigabile anche senza JavaScript).
SCRIPT_INDICE = """<script>
(function () {
  var campo = document.getElementById("cerca-comune");
  if (!campo) return;
  var norm = function (s) { return String(s || "").normalize("NFD").replace(/[\\u0300-\\u036f]/g, "").toLowerCase(); };
  var voci = Array.prototype.slice.call(document.querySelectorAll(".indice-comuni li"));
  var sezioni = Array.prototype.slice.call(document.querySelectorAll(".provincia"));
  var esito = document.getElementById("esito-ricerca");
  campo.addEventListener("input", function () {
    var q = norm(campo.value.trim()), n = 0;
    voci.forEach(function (li) { var ok = !q || norm(li.getAttribute("data-nome")).indexOf(q) !== -1; li.hidden = !ok; if (ok) n++; });
    sezioni.forEach(function (s) { s.hidden = !s.querySelector(".indice-comuni li:not([hidden])"); });
    esito.textContent = q ? (n === 1 ? "1 comune trovato" : n + " comuni trovati") : "";
  });
  campo.addEventListener("keydown", function (e) {
    if (e.key !== "Enter") return;
    // Prima il comune il cui nome inizia con il testo scritto, poi il primo che lo contiene.
    var q = norm(campo.value.trim());
    var visibili = voci.filter(function (li) { return !li.hidden; });
    var scelto = visibili.filter(function (li) { return norm(li.getAttribute("data-nome")).indexOf(q) === 0; })[0] || visibili[0];
    if (scelto) { e.preventDefault(); location.href = scelto.querySelector("a").href; }
  });
})();
</script>
"""


def pagina_indice(per_provincia, conteggi, ctx):
    base = "../"
    titolo = "Appalti pubblici in Toscana: tutti i comuni"
    descrizione = ("Affidamenti diretti, bandi ed esiti di gara per ciascuno dei 273 comuni toscani, "
                   "dalla pubblicità legale di ANAC. Scegli il tuo comune.")
    briciole = [("Appalti Toscana", f"{SITO}/"), ("Comuni", None)]
    out = TESTA.format(titolo=esc(titolo), descrizione=esc(descrizione), canonical=f"{SITO}/comuni/", base=base,
                       robots="", extra_head=json_ld_briciole(briciole))
    out += intestazione(base, [("Appalti Toscana", base), ("Comuni", None)])
    province = sorted(per_provincia, key=lambda p: p.lower())
    salti = "".join(f'<a href="#{esc(p.lower().replace(" ", "-"))}">{esc(p)}</a>' for p in province)
    out += f"""<header class="testata"><div class="contenitore">
<p class="occhiello">273 comuni · 10 province</p>
<h1>Appalti pubblici per comune</h1>
<p class="sottotitolo">Scegli il tuo comune per vedere chi vince gli appalti, quanto viene affidato e quali gare sono aperte.</p>
<div class="ricerca-comuni">
<label for="cerca-comune" class="vh">Cerca un comune</label>
<input type="search" id="cerca-comune" class="campo campo-ricerca" placeholder="Scrivi il nome del comune…" autocomplete="off" spellcheck="false">
<p class="esito-ricerca" id="esito-ricerca" aria-live="polite"></p>
</div>
<nav class="salti" aria-label="Vai alla provincia">{salti}</nav>
</div></header>
<main class="contenitore" id="contenuto" tabindex="-1">
"""
    for prov in province:
        ancora = prov.lower().replace(" ", "-")
        comuni_p = sorted(per_provincia[prov], key=lambda x: x["nome"])
        out += (f'<section class="provincia" aria-labelledby="{esc(ancora)}"><h2 id="{esc(ancora)}">Provincia di {esc(prov)} '
                f'<span class="conta">{len(comuni_p)} comuni</span></h2>\n<ul class="indice-comuni">\n')
        for c in comuni_p:
            n = conteggi.get(c["slug"], 0)
            sub = f'<span class="cifre">{plur(n, "avviso", "avvisi")}</span>' if n else '<span class="cifre">nessun avviso</span>'
            out += (f'<li data-nome="{esc(c["nome"])}"{" class=" + chr(34) + "zero" + chr(34) if not n else ""}>'
                    f'<a href="{base}comune/{esc(c["slug"])}/"><span class="nome">{esc(c["nome"])}</span> {sub}</a></li>\n')
        out += "</ul></section>\n"
    out += "</main>\n" + SCRIPT_INDICE + PIEDE.format(repo=REPO, analytics=snippet_analytics())
    return out


def sitemap(voci):
    righe = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lastmod in voci:
        righe.append(f"<url><loc>{esc(loc)}</loc><lastmod>{lastmod}</lastmod></url>")
    righe.append("</urlset>")
    return "\n".join(righe) + "\n"


# ---------- stile e script condivisi (usati anche dalla home, docs/index.html) ----------

STILE = """/* Stile condiviso di Appalti Toscana: home, indice dei comuni e pagine per comune.
   File generato da genera_sito.py: modifica li', non qui. */
:root { color-scheme: light;
  --carta:#F4F5F8; --superficie:#FFFFFF; --superficie-2:#F8F9FB; --inchiostro:#161B26; --tenue:#5B6271;
  --timbro:#3D44A8; --timbro-scuro:#2C3290; --timbro-tenue:#ECEDFA; --urgente:#B42318; --urgente-tenue:#FDECEA;
  --verde:#1F7A4D; --verde-tenue:#E4F4EC; --filo:rgba(22,27,38,.10); --filo-forte:rgba(22,27,38,.2);
  --ombra:0 1px 2px rgba(22,27,38,.05), 0 8px 26px -12px rgba(22,27,38,.18); --ombra-alta:0 18px 50px -12px rgba(22,27,38,.35);
  --vetro:rgba(244,245,248,.85);
  --testo:"Atkinson Hyperlegible", system-ui, -apple-system, "Segoe UI", sans-serif;
  --display:"Barlow Condensed", "Arial Narrow", system-ui, sans-serif; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { color-scheme: dark;
  --carta:#11141B; --superficie:#1A1F2A; --superficie-2:#1F2532; --inchiostro:#E8EAF0; --tenue:#A0A7B6;
  --timbro:#9BA3FF; --timbro-scuro:#B9BFFF; --timbro-tenue:rgba(155,163,255,.15); --urgente:#FF8A80; --urgente-tenue:rgba(255,138,128,.14);
  --verde:#6FD3A0; --verde-tenue:rgba(111,211,160,.14); --filo:rgba(232,234,240,.10); --filo-forte:rgba(232,234,240,.22);
  --ombra:0 1px 2px rgba(0,0,0,.3), 0 10px 30px -12px rgba(0,0,0,.6); --ombra-alta:0 18px 50px -10px rgba(0,0,0,.75); --vetro:rgba(17,20,27,.85); } }
:root[data-theme="dark"] { color-scheme: dark;
  --carta:#11141B; --superficie:#1A1F2A; --superficie-2:#1F2532; --inchiostro:#E8EAF0; --tenue:#A0A7B6;
  --timbro:#9BA3FF; --timbro-scuro:#B9BFFF; --timbro-tenue:rgba(155,163,255,.15); --urgente:#FF8A80; --urgente-tenue:rgba(255,138,128,.14);
  --verde:#6FD3A0; --verde-tenue:rgba(111,211,160,.14); --filo:rgba(232,234,240,.10); --filo-forte:rgba(232,234,240,.22);
  --ombra:0 1px 2px rgba(0,0,0,.3), 0 10px 30px -12px rgba(0,0,0,.6); --ombra-alta:0 18px 50px -10px rgba(0,0,0,.75); --vetro:rgba(17,20,27,.85); }
* { box-sizing:border-box; }
html { -webkit-text-size-adjust:100%; scroll-padding-top:80px; scroll-behavior:smooth; }
body { margin:0; background:var(--carta); color:var(--inchiostro); font:400 1.0625rem/1.55 var(--testo); }
a { color:var(--timbro); text-underline-offset:.2em; }
:focus-visible { outline:3px solid var(--timbro); outline-offset:2px; border-radius:6px; }
[hidden] { display:none !important; }
.contenitore { max-width:70rem; margin:0 auto; padding:0 1.25rem; }
.vh { position:absolute !important; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; }
.salta { position:absolute; left:12px; top:-60px; z-index:3000; background:var(--timbro); color:#fff; padding:10px 14px; border-radius:10px; font-weight:700; text-decoration:none; }
.salta:focus { top:12px; }

/* Barra superiore */
.barra { position:sticky; top:0; z-index:1000; background:var(--vetro); -webkit-backdrop-filter:saturate(1.5) blur(14px); backdrop-filter:saturate(1.5) blur(14px); border-bottom:1px solid var(--filo); }
.barra .contenitore { display:flex; align-items:center; gap:.75rem; min-height:3.9rem; }
.marchio { display:inline-flex; align-items:center; gap:.6rem; font:700 1.45rem/1 var(--display); letter-spacing:.005em; color:var(--inchiostro); text-decoration:none; min-height:44px; white-space:nowrap; }
.marchio-icona { width:30px; height:30px; border-radius:9px; background:var(--timbro) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Cpath d='M9 10h14M9 16h14M9 22h9' stroke='white' stroke-width='2.6' stroke-linecap='round'/%3E%3C/svg%3E") center/100% no-repeat; box-shadow:0 4px 12px -4px rgba(61,68,168,.6); }
.navigazione { display:flex; gap:.25rem; margin-left:auto; }
.navigazione a { white-space:nowrap; font-weight:700; font-size:.95rem; color:var(--tenue); text-decoration:none; padding:.55rem .9rem; border-radius:999px; min-height:44px; display:inline-flex; align-items:center; }
.navigazione a:hover { color:var(--inchiostro); background:var(--superficie); }
.navigazione a[aria-current="page"] { color:var(--timbro); background:var(--timbro-tenue); }

/* Pulsanti e campi */
.btn { display:inline-flex; align-items:center; justify-content:center; gap:.45rem; font:700 .95rem/1.2 var(--testo); color:var(--inchiostro); background:var(--superficie);
  border:1px solid var(--filo-forte); border-radius:12px; padding:.6rem 1rem; min-height:44px; text-decoration:none; cursor:pointer; transition:background .15s ease, border-color .15s ease, transform .1s ease; }
.btn:hover { border-color:var(--timbro); color:var(--timbro); }
.btn:active { transform:translateY(1px); }
.btn svg { width:16px; height:16px; flex:none; }
.btn-primario { background:var(--timbro); border-color:var(--timbro); color:#fff; }
.btn-primario:hover { background:var(--timbro-scuro); border-color:var(--timbro-scuro); color:#fff; }
:root[data-theme="dark"] .btn-primario, :root[data-theme="dark"] .btn-primario:hover { color:#11141B; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .btn-primario, :root:not([data-theme="light"]) .btn-primario:hover { color:#11141B; } }
.btn-icona { width:44px; padding:0; flex:none; }
.btn-icona svg { width:20px; height:20px; }
.campo { width:100%; font:400 1rem/1.3 var(--testo); color:var(--inchiostro); background:var(--superficie); border:1px solid var(--filo-forte); border-radius:12px; padding:.7rem .85rem; min-height:48px; }
.campo:focus { outline:none; border-color:var(--timbro); box-shadow:0 0 0 3px var(--timbro-tenue); }
.campo-ricerca { padding-left:2.6rem; background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%238A90A0' stroke-width='2.2' stroke-linecap='round'%3E%3Ccircle cx='11' cy='11' r='7'/%3E%3Cpath d='M20 20l-3.5-3.5'/%3E%3C/svg%3E"); background-repeat:no-repeat; background-position:.85rem center; background-size:1.15rem; }

/* Briciole e testata */
.briciole { font-size:.9rem; padding:.9rem 0 0; }
.briciole ol { list-style:none; margin:0; padding:0; display:flex; flex-wrap:wrap; gap:.25rem .5rem; color:var(--tenue); }
.briciole li + li::before { content:"›"; margin-right:.5rem; }
.briciole a { color:var(--tenue); }
.briciole a:hover { color:var(--timbro); }
.testata { padding:1.75rem 0 .5rem; }
.occhiello { display:inline-block; margin:0 0 .7rem; font-size:.85rem; font-weight:700; color:var(--timbro); background:var(--timbro-tenue); border-radius:999px; padding:.3rem .8rem; }
h1 { font:700 clamp(2.3rem,6vw,3.6rem)/1 var(--display); letter-spacing:-.01em; margin:0 0 .7rem; text-wrap:balance; }
.sottotitolo { margin:0; max-width:44em; font-size:1.125rem; color:var(--tenue); }
.aggiornato { margin:.8rem 0 0; color:var(--tenue); font-size:.9375rem; display:inline-flex; align-items:center; gap:.5rem; }
.aggiornato::before { content:""; width:.55rem; height:.55rem; border-radius:50%; background:var(--verde); box-shadow:0 0 0 3px var(--verde-tenue); }
h2 { font:700 1.75rem/1.1 var(--display); margin:0 0 .5rem; display:flex; align-items:baseline; flex-wrap:wrap; gap:.25rem .6rem; }
h2 .conta { font:700 .85rem/1 var(--testo); color:var(--tenue); background:var(--superficie); border:1px solid var(--filo); border-radius:999px; padding:.3rem .6rem; }
.aiuto, .nota { margin:.25rem 0 .9rem; color:var(--tenue); font-size:.9375rem; max-width:64ch; }

/* Numeri chiave */
.numeri { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:.75rem; margin:1.5rem 0 .5rem; }
.numeri div { background:var(--superficie); border:1px solid var(--filo); border-radius:18px; padding:1rem 1.15rem; box-shadow:var(--ombra); }
.numeri dt { color:var(--tenue); font-size:.9rem; font-weight:700; }
.numeri dd { margin:.3rem 0 0; font:700 clamp(1.8rem,4vw,2.5rem)/1 var(--display); font-variant-numeric:tabular-nums; }
.numeri dd small { font-size:.55em; font-weight:600; color:var(--tenue); letter-spacing:0; }
.numeri .evidenza { background:var(--timbro-tenue); border-color:transparent; }
.numeri .evidenza dd { color:var(--timbro); }

/* Indice delle sezioni */
.indice-sezioni { position:sticky; top:3.9rem; z-index:50; display:flex; gap:.5rem; overflow-x:auto; scrollbar-width:none; padding:.75rem 0; margin:1rem 0 .5rem; background:var(--carta); }
.indice-sezioni::-webkit-scrollbar { display:none; }
.indice-sezioni a { flex:none; display:inline-flex; align-items:center; gap:.45rem; font-weight:700; font-size:.92rem; color:var(--inchiostro); text-decoration:none;
  background:var(--superficie); border:1px solid var(--filo-forte); border-radius:999px; padding:.5rem .95rem; min-height:44px; }
.indice-sezioni a:hover { border-color:var(--timbro); color:var(--timbro); }
.conta { font-size:.78rem; font-variant-numeric:tabular-nums; background:var(--carta); border-radius:999px; padding:.1rem .45rem; color:var(--tenue); }

/* Sezioni e avvisi */
.blocco { margin-top:2.25rem; }
.elenco { list-style:none; margin:0; padding:0; display:grid; gap:.75rem; }
.avviso { display:grid; grid-template-columns:6.75rem minmax(0,1fr) auto; gap:1.1rem; align-items:start; background:var(--superficie); border:1px solid var(--filo); border-radius:18px; padding:1.1rem 1.2rem; box-shadow:var(--ombra); transition:border-color .15s ease, box-shadow .15s ease; }
.avviso:hover { border-color:var(--filo-forte); box-shadow:var(--ombra-alta); }
.quando { background:var(--superficie-2); border:1px solid var(--filo); border-radius:12px; padding:.55rem .6rem; text-align:center; font:700 1.15rem/1.1 var(--display); }
.quando .et { display:block; font:600 .78rem/1.2 var(--testo); color:var(--tenue); text-transform:uppercase; letter-spacing:.05em; margin-bottom:.2rem; }
.etichetta-tipo { display:inline-block; font-size:.78rem; font-weight:700; color:var(--timbro); background:var(--timbro-tenue); border-radius:999px; padding:.2rem .6rem; margin-bottom:.4rem; }
.oggetto { font:700 1.0625rem/1.4 var(--testo); margin:0 0 .3rem; max-width:64ch; overflow-wrap:anywhere; }
.ente { margin:0 0 .45rem; color:var(--tenue); }
.dettagli { display:flex; flex-wrap:wrap; gap:.35rem .5rem; list-style:none; margin:0; padding:0; font-size:.9rem; }
.dettagli li { background:var(--superficie-2); border:1px solid var(--filo); border-radius:8px; padding:.15rem .5rem; color:var(--tenue); }
.dettagli .valore { color:var(--inchiostro); font-weight:700; background:var(--verde-tenue); border-color:transparent; }
.dettagli .vincitore strong { color:var(--inchiostro); }
.dettagli .cig { font-family:ui-monospace, Consolas, monospace; font-size:.82rem; }
.collegamenti { display:flex; flex-direction:column; align-items:stretch; gap:.5rem; min-width:11rem; }
.classifica { list-style:none; margin:0; padding:.4rem; background:var(--superficie); border:1px solid var(--filo); border-radius:18px; box-shadow:var(--ombra); counter-reset:pos; }
.classifica li { display:grid; grid-template-columns:2rem minmax(0,1fr) auto; grid-template-rows:auto auto; column-gap:.75rem; row-gap:.35rem; align-items:center; padding:.7rem .8rem; counter-increment:pos; }
.classifica li + li { border-top:1px solid var(--filo); }
.classifica li::before { content:counter(pos); grid-row:1 / 3; font:700 1.25rem/1 var(--display); color:var(--tenue); width:2rem; height:2rem; display:grid; place-items:center; background:var(--superficie-2); border-radius:50%; }
.classifica .nome { font-weight:700; overflow-wrap:anywhere; }
.classifica .cifre { color:var(--tenue); font-size:.9rem; white-space:nowrap; font-variant-numeric:tabular-nums; }
.barra-rel { grid-column:2 / 4; height:6px; border-radius:999px; background:var(--superficie-2); overflow:hidden; }
.barra-rel i { display:block; height:100%; border-radius:999px; background:var(--timbro); opacity:.75; }
.vuoto { background:var(--superficie); border:1px solid var(--filo); border-radius:18px; padding:1.5rem; margin-top:1.5rem; color:var(--tenue); box-shadow:var(--ombra); }
.vuoto p { margin:0 0 .5rem; }
.vuoto strong { color:var(--inchiostro); }
.azioni, .torna { display:flex; flex-wrap:wrap; gap:.5rem; margin-top:1rem; }
.torna { margin-top:2.5rem; }

/* Indice dei comuni */
.ricerca-comuni { max-width:34rem; margin-top:1.4rem; }
.esito-ricerca { margin:.4rem 0 0; min-height:1.4em; font-size:.9rem; color:var(--tenue); }
.salti { display:flex; flex-wrap:wrap; gap:.4rem; margin:.4rem 0 0; }
.salti a { font-weight:700; font-size:.9rem; text-decoration:none; color:var(--inchiostro); background:var(--superficie); border:1px solid var(--filo-forte); border-radius:999px; padding:.45rem .85rem; min-height:40px; display:inline-flex; align-items:center; }
.salti a:hover { border-color:var(--timbro); color:var(--timbro); }
.provincia { margin-top:2.25rem; }
.indice-comuni { list-style:none; margin:.75rem 0 0; padding:0; display:grid; grid-template-columns:repeat(auto-fill,minmax(14rem,1fr)); gap:.5rem; }
.indice-comuni a { display:flex; justify-content:space-between; align-items:center; gap:.75rem; min-height:48px; background:var(--superficie); border:1px solid var(--filo); border-radius:12px; padding:.6rem .9rem; text-decoration:none; color:var(--inchiostro); transition:border-color .15s ease, transform .15s ease, box-shadow .15s ease; }
.indice-comuni a:hover { border-color:var(--timbro); transform:translateY(-1px); box-shadow:var(--ombra); }
.indice-comuni .nome { font-weight:700; }
.indice-comuni .cifre { color:var(--timbro); background:var(--timbro-tenue); font-size:.8rem; font-weight:700; border-radius:999px; padding:.15rem .55rem; white-space:nowrap; }
.indice-comuni .zero .cifre { color:var(--tenue); background:var(--superficie-2); font-weight:400; }
.indice-comuni .zero .nome { font-weight:400; color:var(--tenue); }

/* Piede */
.piede { margin-top:3.5rem; border-top:1px solid var(--filo); padding:1.75rem 0 3rem; font-size:.9375rem; color:var(--tenue); background:var(--superficie); }
.piede p { max-width:70ch; margin:.5rem 0; }

/* Avvisi temporanei */
.toast-zona { position:fixed; left:50%; bottom:20px; transform:translateX(-50%); z-index:3000; display:grid; gap:8px; width:min(92vw,420px); pointer-events:none; }
.toast { background:var(--inchiostro); color:var(--carta); border-radius:14px; padding:.75rem 1rem; font-weight:700; box-shadow:var(--ombra-alta); }

@media (max-width:760px) {
  html { scroll-padding-top:0; }
  .barra { position:static; }
  .indice-sezioni { top:0; }
  .contenitore { padding:0 .9rem; }
  .marchio { font-size:1.3rem; }
  .navigazione a { padding:.5rem .65rem; }
  .numeri { grid-template-columns:1fr 1fr; }
  .avviso { grid-template-columns:5.25rem minmax(0,1fr); gap:.8rem; padding:1rem; }
  .quando { font-size:1rem; }
  .collegamenti { grid-column:1 / -1; flex-direction:row; flex-wrap:wrap; min-width:0; }
  .collegamenti .btn { flex:1 1 auto; }
  h2 { font-size:1.55rem; }
}
/* Telefoni: marchio e tema sulla prima riga, sezioni del sito sotto, a tutta larghezza */
@media (max-width:520px) {
  .barra .contenitore { flex-wrap:wrap; gap:.25rem .5rem; padding-top:.35rem; padding-bottom:.6rem; }
  .navigazione { order:3; width:100%; margin-left:0; background:var(--superficie-2); border:1px solid var(--filo); border-radius:14px; padding:3px; }
  .navigazione a { flex:1; justify-content:center; border-radius:11px; }
  #bottone-tema { margin-left:auto; }
}
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { transition:none !important; animation:none !important; } html { scroll-behavior:auto; } }
"""

TEMA_JS = """/* Tema chiaro / scuro / automatico, condiviso da tutte le pagine. File generato da genera_sito.py. */
(function () {
  var TEMI = ["auto", "light", "dark"];
  var NOMI = { auto: "Tema automatico", light: "Tema chiaro", dark: "Tema scuro" };
  var ICONE = {
    auto: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor"/></svg>',
    light: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
    dark: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/></svg>'
  };
  function salvato() { try { return localStorage.getItem("tema") || "auto"; } catch (e) { return "auto"; } }
  function applica(t) {
    if (t === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem("tema", t); } catch (e) {}
    var b = document.getElementById("bottone-tema");
    if (b) { b.innerHTML = ICONE[t]; b.title = NOMI[t]; b.setAttribute("aria-label", NOMI[t] + " (clic per cambiare)"); }
  }
  function avvia() {
    applica(salvato());
    var b = document.getElementById("bottone-tema");
    if (b) b.addEventListener("click", function () { applica(TEMI[(TEMI.indexOf(salvato()) + 1) % TEMI.length]); });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", avvia); else avvia();
})();
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
    w.scrivi(out / "tema.js", TEMA_JS)
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
