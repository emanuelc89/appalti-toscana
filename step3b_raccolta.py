"""
Step 3 (versione corretta) - Raccolta completa con PAGINAZIONE A TOKEN + filtro Toscana in locale.

Differenze rispetto alla prima versione:
  - il parametro 'page' viene ignorato dal servizio: si usa direzionePaginazione=AVANTI + tokenPaginazione
  - pagine da 200 risultati
  - raccoglie 4 tipologie: 4=bandi, 2=pre-informazione indittivi, 5b=indagini di mercato sotto soglia,
    5a=indagini di mercato sopra soglia (gli esiti 7 e 8a li vediamo al passo successivo)

Requisiti: Python 3.10+, requests. Mettilo accanto agli altri script (usa anac_test/comuni.json gia' scaricato).
"""
import json
import re
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import requests

BASE = "https://pubblicitalegale.anticorruzione.it/api/v0"
OUT = Path(__file__).parent / "anac_test"
OUT.mkdir(exist_ok=True)

PAUSA = 1.2
GIORNI = 14
SIZE = 200
MAX_PAGINE = 60
COMUNI_URL = "https://raw.githubusercontent.com/matteocontrini/comuni-json/master/comuni.json"
COMUNI_FILE = OUT / "comuni.json"

TEMPLATES = {
    "4": "Bandi",
    "2": "Avvisi di pre-informazione indittivi",
    "5b": "Indagini di mercato sotto soglia",
    "5a": "Indagini di mercato sopra soglia",
}

EMPOLESE = ["Capraia e Limite", "Castelfiorentino", "Cerreto Guidi", "Certaldo", "Empoli",
            "Fucecchio", "Gambassi Terme", "Montaione", "Montelupo Fiorentino",
            "Montespertoli", "Vinci"]

session = requests.Session()
session.headers.update({
    "Accept": "application/json",
    "User-Agent": "AppaltiToscana-test/0.1 (progetto civico; contatto: emanuelc89 su GitHub)",
})


def norm(s) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^A-Za-z0-9]+", " ", s.upper()).strip()


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


def carica_comuni():
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
    province = set()
    for c in comuni:
        n = norm(c["nome"])
        reg = c["regione"]["nome"]
        regioni_per_nome[n].add(reg)
        if reg == "Toscana":
            toscani[n] = c["provincia"]["nome"]
            province.add(norm(c["provincia"]["nome"]))
    return toscani, province, regioni_per_nome


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
    print(f"   prima pagina: richiesti {SIZE}, restituiti {len(cont)}, count dichiarato {count}")
    visti = {a.get("idAvviso"): a for a in cont}
    token = dati.get("lastPaginationToken")
    pagina = 1
    while token and cont and pagina < MAX_PAGINE:
        dati = get("/avvisi-full-text", params(template, start, end, token))
        if not dati:
            print("   Raccolta interrotta (errore): dati parziali.")
            break
        cont = dati.get("content") or []
        nuovi = 0
        for a in cont:
            if a.get("idAvviso") not in visti:
                visti[a.get("idAvviso")] = a
                nuovi += 1
        print(f"   pagina {pagina}: {len(cont)} risultati, {nuovi} nuovi (totale {len(visti)})")
        if nuovi == 0 or len(cont) < SIZE:
            break
        token = dati.get("lastPaginationToken")
        pagina += 1
    if count and abs(len(visti) - count) > 0.1 * count:
        print(f"   NOTA: raccolti {len(visti)} contro un count dichiarato di {count} (il count e' una stima).")
    return list(visti.values())


def record(a: dict, template: str) -> dict:
    def tutti(k):
        return list(dict.fromkeys(str(x) for x in trova(a, k) if x not in (None, "")))

    valori = [x for x in trova(a, "valore_complessivo_stimato") if isinstance(x, (int, float))]
    return {
        "template": template,
        "idAvviso": a.get("idAvviso"),
        "idAppalto": a.get("idAppalto"),
        "codiceScheda": a.get("codiceScheda"),
        "pubblicato": (a.get("dataPubblicazione") or "")[:10],
        "scadenza": (a.get("dataScadenza") or "")[:10],
        "committente": (tutti("denominazione_amministrazione") or [None])[0],
        "titolo": (tutti("titolo") or [None])[0],
        "cig": tutti("cig"),
        "luoghi": tutti("luogo_istat"),
        "nuts": tutti("luogo_nuts"),
        "valore_max": max(valori) if valori else None,
    }


def classifica(r, toscani, province, regioni_per_nome):
    nuts_n = [norm(x) for x in r["nuts"]]
    luoghi_n = [norm(x) for x in r["luoghi"]]
    if any(n in province for n in nuts_n):
        return "A_luogo_confermato"
    if not nuts_n:
        comuni_t = [lu for lu in luoghi_n if lu in toscani]
        if comuni_t:
            if any(len(regioni_per_nome[lu]) > 1 for lu in comuni_t):
                return "C_ambiguo"
            return "B_comune_toscano"
    if "TOSCAN" in norm(r["committente"]):
        return "D_solo_committente"
    return None


def provincia_di(r, toscani, province):
    for n in (norm(x) for x in r["nuts"]):
        if n in province:
            return n.title()
    for lu in (norm(x) for x in r["luoghi"]):
        if lu in toscani:
            return toscani[lu]
    return "?"


def riga(r):
    luoghi = ", ".join(r["luoghi"][:3])
    if len(r["luoghi"]) > 3:
        luoghi += f" (+{len(r['luoghi']) - 3} luoghi)"
    titolo = (r["titolo"] or "")[:60]
    return (f"{r['pubblicato']} | {(r['categoria'] or '-')[0]} | {r['committente']} | {luoghi} "
            f"| scad {r['scadenza']} | {r['valore_max']} | {titolo}")


def main():
    t0 = time.time()
    toscani, province, regioni_per_nome = carica_comuni()
    print(f"Comuni toscani: {len(toscani)} | province: {sorted(province)}")

    oggi = date.today()
    start = (oggi - timedelta(days=GIORNI)).strftime("%d/%m/%Y")
    end = oggi.strftime("%d/%m/%Y")

    tutti = []
    for template, nome in TEMPLATES.items():
        print(f"\n=== Template {template}: {nome} ({start} - {end}) ===")
        grezzi = raccogli(template, start, end)
        ultimi = {}
        versioni = Counter()
        for a in grezzi:
            r = record(a, template)
            chiave = r["idAppalto"] or r["idAvviso"]
            versioni[chiave] += 1
            if chiave not in ultimi or r["pubblicato"] >= ultimi[chiave]["pubblicato"]:
                ultimi[chiave] = r
        multi = sum(1 for v in versioni.values() if v > 1)
        unici = list(ultimi.values())
        for r in unici:
            r["categoria"] = classifica(r, toscani, province, regioni_per_nome)
        tosc = [r for r in unici if r["categoria"]]
        per_cat = Counter(r["categoria"] for r in tosc)
        dettaglio = ", ".join(f"{k[0]}={v}" for k, v in sorted(per_cat.items())) or "nessuna"
        print(f"   RIEPILOGO: avvisi {len(grezzi)} | procedure uniche {len(unici)} "
              f"(con piu' versioni: {multi}) | Toscana {len(tosc)} [{dettaglio}]")
        tutti.extend(unici)

    (OUT / "harvest_unici.json").write_text(json.dumps(tutti, ensure_ascii=False, indent=1), encoding="utf-8")
    tosc_tutti = [r for r in tutti if r["categoria"]]
    (OUT / "toscana.json").write_text(json.dumps(tosc_tutti, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n=== Per provincia (A, B, C; tutte le tipologie) ===")
    per_prov = Counter(provincia_di(r, toscani, province) for r in tosc_tutti if r["categoria"][0] in "ABC")
    for p, n in per_prov.most_common():
        print(f"  {p}: {n}")

    print("\n=== Bandi (template 4): per giorno, Italia / Toscana ===")
    b4 = [r for r in tutti if r["template"] == "4"]
    tot_g = Counter(r["pubblicato"] for r in b4)
    tos_g = Counter(r["pubblicato"] for r in b4 if r["categoria"])
    for g in sorted(tot_g):
        print(f"  {g}: {tot_g[g]} / {tos_g.get(g, 0)}")

    print("\n=== Empolese Valdelsa (tutte le tipologie) ===")
    ev = {norm(x) for x in EMPOLESE}
    n_ev = 0
    for r in sorted(tutti, key=lambda x: x["pubblicato"], reverse=True):
        if any(norm(lu) in ev for lu in r["luoghi"]) or "EMPOLESE" in norm(r["committente"]):
            print(f"  [{r['template']}] " + riga(r))
            n_ev += 1
    print(f"  Totale: {n_ev}")

    print("\n=== Controllo qualita': luoghi non toscani dentro avvisi con provincia toscana ===")
    sconosciuti = Counter()
    for r in tutti:
        if r["categoria"] == "A_luogo_confermato":
            for lu in r["luoghi"]:
                if norm(lu) not in toscani:
                    sconosciuti[lu] += 1
    for lu, n in sconosciuti.most_common(15):
        print(f"  {lu}: {n}")
    if not sconosciuti:
        print("  nessuno")

    for template, nome in TEMPLATES.items():
        righe = sorted([r for r in tosc_tutti if r["template"] == template],
                       key=lambda x: x["pubblicato"], reverse=True)
        print(f"\n=== Template {template} ({nome}): prime 25 procedure toscane ===")
        for r in righe[:25]:
            print("  " + riga(r))
        if not righe:
            print("  nessuna")

    print(f"\nTempo totale: {time.time() - t0:.0f} secondi")
    print("Fatto. Incolla qui in chat tutto l'output della console.")


if __name__ == "__main__":
    main()
