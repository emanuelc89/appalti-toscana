"""
aggiorna.py - Pipeline di produzione: raccolta incrementale ANAC (Toscana) + archivio mensile.

Uso:
    python aggiorna.py                 # ultimi 3 giorni (uso quotidiano)
    python aggiorna.py --giorni 14     # primo popolamento (circa 10-12 minuti)
    python aggiorna.py --senza-cpv     # salta i dettagli per i CPV (test veloci)

Output (cartella 'docs/dati', servita da GitHub Pages):
    archivio/AAAA-MM-GG.json   record toscani pubblicati quel giorno, unione tra una esecuzione e l'altra
                               (un file per giorno: la cronologia git cresce poco)
    recenti.json            opportunita' (bandi, indagini, pre-informazioni) degli ultimi 60 giorni
    comuni.json             i 273 comuni toscani con slug e provincia
    meta.json               data di generazione, finestra, conteggi
Cache (cartella 'cache'): comuni.json, cpv_cache.json

Categorie geografiche ('geo'):
    A provincia toscana indicata | B comune toscano univoco (provincia assente o non riconosciuta)
    C comune toscano omonimo di altre regioni | D nessun luogo, committente con 'TOSCAN' nel nome
    M multiregionale (3 o piu' luoghi non toscani)

Privacy: nomi degli aggiudicatari solo con codice fiscale a 11 cifre (societa'); i codici fiscali non
         vengono mai salvati.
Nota: una rettifica pubblicata un altro giorno lascia la versione precedente nel file del suo giorno;
      recenti.json deduplica sempre per procedura, tenendo la versione piu' recente.

Requisiti: Python 3.10+, requests.
"""
import argparse
import html
import json
import re
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = "https://pubblicitalegale.anticorruzione.it/api/v0"
AQUI = Path(__file__).parent
DATI = AQUI / "docs" / "dati"
ARCH = DATI / "archivio"
CACHE = AQUI / "cache"
for cartella in (DATI, ARCH, CACHE):
    cartella.mkdir(parents=True, exist_ok=True)

COMUNI_FILE = CACHE / "comuni.json"
CPV_CACHE = CACHE / "cpv_cache.json"
COMUNI_URL = "https://raw.githubusercontent.com/matteocontrini/comuni-json/master/comuni.json"

PAUSA = 1.2
SIZE = 200
MAX_PAGINE = 400

TEMPLATES = [
    ("4", "bando"),
    ("5b", "indagine_sotto_soglia"),
    ("5a", "indagine_sopra_soglia"),
    ("2", "preinformazione"),
    ("7", "esito"),
    ("8a", "affidamento_diretto"),
]
TIPI_CON_CPV = ("bando", "indagine_sotto_soglia", "indagine_sopra_soglia")
TIPI_OPPORTUNITA = TIPI_CON_CPV + ("preinformazione",)

# Sezione dell'URL pubblico di ogni avviso: verificati 4 (bandi), 5b (avvisi), 8a (esiti);
# gli altri seguono la categoria indicata dalla mappa ANAC.
URL_SEZIONE = {"4": "bandi", "2": "bandi", "5a": "avvisi", "5b": "avvisi", "7": "esiti", "8a": "esiti"}

EMPOLESE = ["Capraia e Limite", "Castelfiorentino", "Cerreto Guidi", "Certaldo", "Empoli",
            "Fucecchio", "Gambassi Terme", "Montaione", "Montelupo Fiorentino",
            "Montespertoli", "Vinci"]

session = requests.Session()
session.headers.update({
    "Accept": "application/json",
    "User-Agent": "AppaltiToscana/0.1 (progetto civico; contatto: emanuelc89 su GitHub)",
})


# ---------- utilita' ----------

def norm(s) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^A-Za-z0-9]+", " ", s.upper()).strip()


def slug(s) -> str:
    return norm(s).lower().replace(" ", "-")


def pulisci(s):
    if s is None:
        return None
    s = html.unescape(str(s))
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def trova(obj, chiave, trovati=None):
    if trovati is None:
        trovati = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == chiave and not isinstance(v, (dict, list)):
                trovati.append(v)
            else:
                trova(v, chiave, trovati)
    elif isinstance(obj, list):
        for x in obj:
            trova(x, chiave, trovati)
    return trovati


def valori_chiave(obj, chiave, trovati=None):
    if trovati is None:
        trovati = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == chiave:
                trovati.append(v)
            else:
                valori_chiave(v, chiave, trovati)
    elif isinstance(obj, list):
        for x in obj:
            valori_chiave(x, chiave, trovati)
    return trovati


def primo(a, k):
    for x in trova(a, k):
        if x not in (None, ""):
            return x
    return None


def lista(a, k):
    return list(dict.fromkeys(str(x) for x in trova(a, k) if x not in (None, "")))


def numeri(a, k):
    return [x for x in trova(a, k) if isinstance(x, (int, float)) and not isinstance(x, bool)]


def get(path, params=None):
    try:
        r = session.get(BASE + path, params=params, timeout=90)
    except requests.RequestException as e:
        print(f"   ERRORE di rete: {e}")
        return None
    if r.status_code != 200:
        print(f"   Status {r.status_code}: {r.text[:300]}")
        return None
    time.sleep(PAUSA)
    try:
        return r.json()
    except ValueError:
        print("   La risposta non e' JSON.")
        return None


# ---------- comuni ----------

def carica_comuni():
    for nome in ("comuni.json", "cpv_cache.json"):
        vecchio = AQUI / "anac_test" / nome
        nuovo = CACHE / nome
        if vecchio.exists() and not nuovo.exists():
            nuovo.write_bytes(vecchio.read_bytes())
    if not COMUNI_FILE.exists():
        print("Scarico l'elenco dei comuni...")
        try:
            r = requests.get(COMUNI_URL, timeout=60)
            r.raise_for_status()
            COMUNI_FILE.write_bytes(r.content)
        except requests.RequestException as e:
            print(f"Non riesco a scaricare l'elenco comuni ({e}).")
            raise SystemExit(1)
    comuni = json.loads(COMUNI_FILE.read_text(encoding="utf-8"))
    regioni_per_nome = defaultdict(set)
    toscani = {}
    prov_canon = {}
    tutte_prov = set()
    elenco = []
    for c in comuni:
        n = norm(c["nome"])
        reg = c["regione"]["nome"]
        regioni_per_nome[n].add(reg)
        tutte_prov.add(norm(c["provincia"]["nome"]))
        if reg == "Toscana":
            toscani[n] = c["provincia"]["nome"]
            prov_canon[norm(c["provincia"]["nome"])] = c["provincia"]["nome"]
            elenco.append({"nome": c["nome"], "slug": slug(c["nome"]), "provincia": c["provincia"]["nome"]})
    elenco.sort(key=lambda x: x["nome"])
    return toscani, prov_canon, tutte_prov, regioni_per_nome, elenco


# ---------- raccolta ----------

def params(template, start, end, token=None):
    p = {
        "codiceScheda": template,
        "dataPubblicazioneStart": start,
        "dataPubblicazioneEnd": end,
        "sortField": "dataPubblicazione",
        "sortDirection": "DESC",
        "size": SIZE,
    }
    if token:
        p["direzionePaginazione"] = "AVANTI"
        p["tokenPaginazione"] = token
    return p


def raccogli(template, start, end):
    dati = get("/avvisi-full-text", params(template, start, end))
    if not dati:
        return []
    cont = dati.get("content") or []
    count = dati.get("count")
    print(f"   prima pagina: {len(cont)} risultati, count dichiarato {count}")
    visti = {a.get("idAvviso"): a for a in cont}
    token = dati.get("lastPaginationToken")
    pagina = 1
    while token and cont and len(cont) >= SIZE and pagina < MAX_PAGINE:
        dati = get("/avvisi-full-text", params(template, start, end, token))
        if not dati:
            print("   Raccolta interrotta (errore): dati parziali, l'archivio resta comunque coerente.")
            break
        cont = dati.get("content") or []
        nuovi = 0
        for a in cont:
            if a.get("idAvviso") not in visti:
                visti[a.get("idAvviso")] = a
                nuovi += 1
        if pagina % 10 == 0:
            print(f"   pagina {pagina}: totale {len(visti)}")
        if nuovi == 0:
            break
        token = dati.get("lastPaginationToken")
        pagina += 1
    print(f"   raccolti {len(visti)} avvisi in {pagina} pagine")
    if count and abs(len(visti) - count) > 0.1 * count:
        print(f"   NOTA: raccolti {len(visti)} contro count dichiarato {count} (il count e' una stima).")
    return list(visti.values())


# ---------- estrazione e classificazione ----------

def estrai(a: dict, template: str, tipo: str) -> dict:
    stimati = numeri(a, "valore_complessivo_stimato")
    vincenti = numeri(a, "valore_offerta_vincente")
    affidati = numeri(a, "valore_affidamento")
    valore = max(stimati + affidati) if (stimati or affidati) else None
    valore_agg = vincenti[0] if len(vincenti) == 1 else None
    ribasso = None
    if len(stimati) == 1 and len(vincenti) == 1 and stimati[0] > 0:
        ribasso = round((1 - vincenti[0] / stimati[0]) * 100, 1)

    agg, nascosti = [], 0
    for chiave_agg in ("aggiudicatari_ad", "aggiudicatari"):
        for elenco in valori_chiave(a, chiave_agg):
            for ad in (elenco if isinstance(elenco, list) else []):
                if not isinstance(ad, dict):
                    continue
                importo = ad.get("importo")
                for s in ad.get("soggetti") or []:
                    cf = str(s.get("codice_fiscale") or "").strip()
                    if re.fullmatch(r"\d{11}", cf):
                        agg.append({"denominazione": pulisci(s.get("denominazione")), "importo": importo})
                    else:
                        nascosti += 1

    pub = (a.get("dataPubblicazione") or "")[:10]
    scad = (a.get("dataScadenza") or "")[:10]
    cig = lista(a, "cig")
    return {
        "id": a.get("idAvviso"),
        "procedura": a.get("idAppalto"),
        "tipo": tipo,
        "template": template,
        "scheda": a.get("codiceScheda"),
        "pubblicato": pub,
        "scadenza": scad or None,
        "anomalia_scadenza": bool(scad and pub and scad < pub),
        "committente": pulisci(primo(a, "denominazione_amministrazione")),
        "titolo": pulisci(primo(a, "titolo") or primo(a, "descrizione")),
        "cig": cig,
        "n_lotti": len(cig) or 1,
        "cpv": [{"codice": None, "descrizione": pulisci(x)} for x in lista(a, "cpv")],
        "valore": valore,
        "valore_aggiudicato": valore_agg,
        "ribasso_pct": ribasso,
        "luoghi": lista(a, "luogo_istat"),
        "nuts": lista(a, "luogo_nuts"),
        "link_documenti": primo(a, "documenti_di_gara_link"),
        "link_ted": primo(a, "link_eform_ted"),
        "url_anac": (f"https://pubblicitalegale.anticorruzione.it/{URL_SEZIONE[template]}/{a.get('idAvviso')}"
                     if template in URL_SEZIONE and a.get("idAvviso") else None),
        "aggiudicatari": agg,
        "aggiudicatari_nascosti": nascosti,
        "stato_anac": a.get("stato"),
    }


def classifica(r, toscani, prov_canon, tutte_prov, regioni_per_nome):
    nuts_n = [norm(x) for x in r["nuts"]]
    luoghi_n = [norm(x) for x in r["luoghi"]]
    nuts_toscana = any(n in prov_canon for n in nuts_n)
    nuts_altre = [n for n in nuts_n if n in tutte_prov and n not in prov_canon]
    comuni_t = [lu for lu in luoghi_n if lu in toscani]
    non_tosc = [lu for lu in luoghi_n if lu not in toscani]
    if nuts_toscana:
        cat = "A"
    elif comuni_t and not nuts_altre:
        cat = "C" if any(len(regioni_per_nome[lu]) > 1 for lu in comuni_t) else "B"
    elif not luoghi_n and not nuts_n and "TOSCAN" in norm(r["committente"]):
        cat = "D"
    else:
        return None
    if cat in ("A", "B", "C") and len(non_tosc) >= 3:
        return "M"
    return cat


def province_record(r, toscani, prov_canon):
    out = []
    for n in (norm(x) for x in r["nuts"]):
        if n in prov_canon and prov_canon[n] not in out:
            out.append(prov_canon[n])
    for lu in (norm(x) for x in r["luoghi"]):
        if lu in toscani and toscani[lu] not in out:
            out.append(toscani[lu])
    return out


def parse_cpv(valori):
    out = []
    for v in valori or []:
        codice, _, desc = str(v).partition("_")
        if codice.isdigit():
            out.append({"codice": codice, "descrizione": pulisci(desc)})
        else:
            out.append({"codice": None, "descrizione": pulisci(v)})
    return out


def arricchisci_cpv(records):
    cache = json.loads(CPV_CACHE.read_text(encoding="utf-8")) if CPV_CACHE.exists() else {}
    da_fare = [r for r in records if r["tipo"] in TIPI_CON_CPV and r["id"] not in cache]
    print(f"\nDettagli da scaricare per i CPV: {len(da_fare)} (in cache: {len(cache)})")
    for i, r in enumerate(da_fare, 1):
        det = get(f"/avvisi/{r['id']}")
        if det:
            cache[r["id"]] = [str(x) for x in trova(det, "cpv") if x]
        if i % 25 == 0:
            print(f"   {i}/{len(da_fare)}")
            CPV_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    CPV_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    for r in records:
        if r["tipo"] in TIPI_CON_CPV and cache.get(r["id"]):
            r["cpv"] = parse_cpv(cache[r["id"]])


# ---------- archivio ----------

def chiave(r):
    return f"{r['template']}:{r['procedura'] or r['id']}"


def ha_codice_cpv(r):
    return any(x.get("codice") for x in r.get("cpv") or [])


def aggiorna_archivio(records):
    per_giorno = defaultdict(list)
    for r in records:
        per_giorno[r["pubblicato"] or "senza-data"].append(r)
    nuovi = nuove_versioni = 0
    for giorno, elenco in per_giorno.items():
        f = ARCH / f"{giorno}.json"
        esistenti = {}
        if f.exists():
            esistenti = {chiave(x): x for x in json.loads(f.read_text(encoding="utf-8"))}
        for r in elenco:
            k = chiave(r)
            vecchio = esistenti.get(k)
            if vecchio is None:
                r["prima_pubblicazione"] = r["pubblicato"]
                esistenti[k] = r
                nuovi += 1
                continue
            r["prima_pubblicazione"] = min(vecchio.get("prima_pubblicazione") or vecchio["pubblicato"],
                                           r["pubblicato"])
            if r["pubblicato"] >= vecchio["pubblicato"]:
                if ha_codice_cpv(vecchio) and not ha_codice_cpv(r) and vecchio["id"] == r["id"]:
                    r["cpv"] = vecchio["cpv"]
                if vecchio["id"] != r["id"]:
                    nuove_versioni += 1
                esistenti[k] = r
        ordinati = sorted(esistenti.values(), key=lambda x: x["id"])
        testo = json.dumps(ordinati, ensure_ascii=False, separators=(",", ":"))
        if not f.exists() or f.read_text(encoding="utf-8") != testo:
            f.write_text(testo, encoding="utf-8")
    return nuovi, nuove_versioni


def scrivi_recenti(oggi):
    soglia = (oggi - timedelta(days=60)).isoformat()
    candidati = {}
    for f in sorted(ARCH.glob("*.json")):
        if f.stem < soglia:
            continue
        for r in json.loads(f.read_text(encoding="utf-8")):
            if r["tipo"] in TIPI_OPPORTUNITA and r["pubblicato"] >= soglia:
                k = chiave(r)
                if k not in candidati or r["pubblicato"] >= candidati[k]["pubblicato"]:
                    candidati[k] = r
    out = sorted(candidati.values(), key=lambda x: (x["pubblicato"], x["id"]), reverse=True)
    (DATI / "recenti.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")),
                                       encoding="utf-8")
    return len(out)


# ---------- stampa ----------

def riga(r):
    luoghi = ", ".join(r["luoghi"][:3])
    if len(r["luoghi"]) > 3:
        luoghi += f" (+{len(r['luoghi']) - 3})"
    agg = "; ".join(f"{x['denominazione']} {x['importo']}" for x in r["aggiudicatari"][:2])
    rib = f" | ribasso {r['ribasso_pct']}%" if r["ribasso_pct"] is not None else ""
    return (f"{r['pubblicato']} | {r['geo']} | {r['committente']} | {luoghi} | {r['valore']} "
            f"| {(r['titolo'] or '')[:60]}{rib}" + (f" | AGG: {agg}" if agg else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--giorni", type=int, default=3, help="giorni indietro da oggi (default 3)")
    ap.add_argument("--senza-cpv", action="store_true", help="non scaricare i dettagli per i CPV")
    args = ap.parse_args()

    t0 = time.time()
    toscani, prov_canon, tutte_prov, regioni_per_nome, elenco_comuni = carica_comuni()
    print(f"Comuni toscani: {len(toscani)} | finestra: ultimi {args.giorni} giorni")

    oggi = date.today()
    start = (oggi - timedelta(days=args.giorni)).strftime("%d/%m/%Y")
    end = oggi.strftime("%d/%m/%Y")

    tutti = []
    stat = {}
    for template, tipo in TEMPLATES:
        print(f"\n=== Template {template} ({tipo}) {start} - {end} ===")
        grezzi = raccogli(template, start, end)
        ultimi = {}
        versioni = Counter()
        for a in grezzi:
            r = estrai(a, template, tipo)
            k = r["procedura"] or r["id"]
            versioni[k] += 1
            if k not in ultimi or r["pubblicato"] >= ultimi[k]["pubblicato"]:
                ultimi[k] = r
        for k, r in ultimi.items():
            r["versioni"] = versioni[k]
            r["geo"] = classifica(r, toscani, prov_canon, tutte_prov, regioni_per_nome)
            r["province"] = province_record(r, toscani, prov_canon) if r["geo"] else []
            r["comuni_toscani"] = [lu for lu in r["luoghi"] if norm(lu) in toscani] if r["geo"] else []
            r["comuni_slug"] = [slug(lu) for lu in r["comuni_toscani"]]
        unici = list(ultimi.values())
        stat[template] = {"raccolti": len(grezzi), "uniche": len(unici),
                          "cat": Counter(r["geo"] for r in unici if r["geo"])}
        tutti.extend(unici)

    tosc = [r for r in tutti if r["geo"]]
    if not args.senza_cpv:
        arricchisci_cpv(tosc)

    nuovi, nuove_versioni = aggiorna_archivio(tosc)
    n_recenti = scrivi_recenti(oggi)
    (DATI / "comuni.json").write_text(json.dumps(elenco_comuni, ensure_ascii=False, indent=1), encoding="utf-8")
    meta = {
        "generato": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fonte": "ANAC - Piattaforma di Pubblicita' a Valore Legale (pubblicitalegale.anticorruzione.it)",
        "licenza_dati": "CC BY-SA 4.0 - https://creativecommons.org/licenses/by-sa/4.0/",
        "attribuzione": "Appalti Toscana (github.com/emanuelc89/appalti-toscana), elaborazione su dati ANAC",
        "finestra_giorni": args.giorni,
        "conteggi": {t: {"raccolti": s["raccolti"], "uniche": s["uniche"], "toscana": dict(s["cat"])}
                     for t, s in stat.items()},
        "archivio_nuovi_record": nuovi,
        "archivio_nuove_versioni": nuove_versioni,
        "recenti": n_recenti,
    }
    (DATI / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n=== RIEPILOGO per tipologia ===")
    for template, tipo in TEMPLATES:
        s = stat[template]
        dett = ", ".join(f"{k}={v}" for k, v in sorted(s["cat"].items())) or "nessuna"
        print(f"  {template:>2} {tipo}: avvisi {s['raccolti']} | uniche {s['uniche']} | Toscana [{dett}]")
    print(f"  Archivio: {nuovi} record nuovi, {nuove_versioni} nuove versioni | recenti.json: {n_recenti} record")

    print("\n=== Copertura comuni toscani (su 273) ===")
    for etichetta, tipi in (("opportunita'", TIPI_OPPORTUNITA), ("esiti", ("esito",)),
                            ("affidamenti diretti", ("affidamento_diretto",))):
        cop = {c for r in tosc if r["tipo"] in tipi and r["geo"] in ("A", "B", "C") for c in r["comuni_slug"]}
        print(f"  {etichetta}: {len(cop)} comuni")

    print("\n=== Controllo: avvisi con comune toscano ESCLUSI dalla Toscana (diagnosi) ===")
    esclusi = [r for r in tutti if not r["geo"] and any(norm(lu) in toscani for lu in r["luoghi"])]
    print(f"  totale: {len(esclusi)}")
    for r in esclusi[:15]:
        print(f"  [{r['template']}] {r['committente']} | luoghi={r['luoghi'][:3]} | nuts={r['nuts'][:3]}")

    print("\n=== Empolese Valdelsa ===")
    ev = {norm(x) for x in EMPOLESE}
    righe_ev = [r for r in sorted(tutti, key=lambda x: x["pubblicato"], reverse=True)
                if any(norm(lu) in ev for lu in r["luoghi"]) or "EMPOLESE" in norm(r["committente"])]
    per_tipo = Counter(r["tipo"] for r in righe_ev)
    print("  " + ", ".join(f"{k}={v}" for k, v in per_tipo.items()))
    for r in righe_ev[:12]:
        print("  " + riga(r))

    print("\n=== Esiti: vincitori e ribasso ===")
    esiti = [r for r in tosc if r["tipo"] == "esito"]
    print(f"  esiti toscani: {len(esiti)} | con aggiudicatari (societa'): "
          f"{sum(1 for r in esiti if r['aggiudicatari'])} | con ribasso: "
          f"{sum(1 for r in esiti if r['ribasso_pct'] is not None)}")
    for r in esiti[:8]:
        print("  " + riga(r))

    print("\n=== Controlli di qualita' ===")
    print(f"  titolo mancante: {sum(1 for r in tosc if not r['titolo'])} su {len(tosc)}")
    print(f"  anomalia scadenza: {sum(1 for r in tosc if r['anomalia_scadenza'])}")
    print(f"  multiregionali (M): {sum(1 for r in tosc if r['geo'] == 'M')} | "
          f"ambigui (C): {sum(1 for r in tosc if r['geo'] == 'C')}")
    ad = [r for r in tosc if r["tipo"] == "affidamento_diretto"]
    print(f"  affidamenti diretti: aggiudicatari pubblicabili {sum(len(r['aggiudicatari']) for r in ad)} | "
          f"nascosti {sum(r['aggiudicatari_nascosti'] for r in ad)}")
    opp = [r for r in tosc if r["tipo"] in TIPI_CON_CPV]
    print(f"  CPV con codice: {sum(1 for r in opp if ha_codice_cpv(r))} su {len(opp)}")

    print(f"\nTempo totale: {time.time() - t0:.0f} secondi")
    print("Fatto. Incolla qui in chat tutto l'output della console.")


if __name__ == "__main__":
    main()
