"""
Step 3 - Raccolta completa dei bandi (ultimi 14 giorni) + filtro Toscana in locale.

Cosa fa:
  1) scarica l'elenco dei comuni italiani (comuni-json, aggiornato al 01/01/2020) e lo salva in anac_test/comuni.json
  2) stampa la mappa delle tipologie ANAC (per scoprire i codici di esiti e altri avvisi)
  3) scarica TUTTI i bandi (codiceScheda=4) degli ultimi 14 giorni, a pagine, con pausa tra le richieste
  4) deduplica per procedura (idAppalto) e classifica la Toscana
  5) salva anac_test/harvest_unici.json e anac_test/toscana.json

Requisiti: Python 3.10+, requests. Metti il file accanto a test_anac.py.
Durata: da pochi secondi a qualche minuto, a seconda di quanto grande e' la pagina massima.
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

PAUSA = 1.2          # secondi tra una richiesta e l'altra
GIORNI = 14          # finestra di raccolta
MAX_PAGINE = 200     # freno di sicurezza
COMUNI_URL = "https://raw.githubusercontent.com/matteocontrini/comuni-json/master/comuni.json"
COMUNI_FILE = OUT / "comuni.json"

# I 11 comuni del Circondario Empolese Valdelsa
EMPOLESE = ["Capraia e Limite", "Castelfiorentino", "Cerreto Guidi", "Certaldo", "Empoli",
            "Fucecchio", "Gambassi Terme", "Montaione", "Montelupo Fiorentino",
            "Montespertoli", "Vinci"]

session = requests.Session()
session.headers.update({
    "Accept": "application/json",
    "User-Agent": "AppaltiToscana-test/0.1 (progetto civico; contatto: emanuelc89 su GitHub)",
})


def norm(s) -> str:
    """Maiuscolo, senza accenti ne' punteggiatura: 'Piandiscò' e "PIANDISCO'" diventano uguali."""
    if not s:
        return ""
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^A-Za-z0-9]+", " ", s.upper()).strip()


def trova(obj, chiave, trovati=None):
    """Tutti i valori scalari di una chiave in un JSON annidato."""
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
        r = session.get(BASE + path, params=params, timeout=60)
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
            print(f"Scaricalo a mano da {COMUNI_URL}")
            print(f"e salvalo come {COMUNI_FILE}, poi rilancia lo script.")
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
    if not toscani:
        nomi = sorted({c["regione"]["nome"] for c in comuni})
        print("ATTENZIONE: nessun comune con regione 'Toscana'. Regioni presenti:", nomi)
        raise SystemExit(1)
    return toscani, province, regioni_per_nome


def params_base(start, end, pagina, size):
    return {
        "codiceScheda": "4",
        "dataPubblicazioneStart": start,
        "dataPubblicazioneEnd": end,
        "sortField": "dataPubblicazione",
        "sortDirection": "DESC",
        "page": pagina,
        "size": size,
    }


def raccogli():
    oggi = date.today()
    start = (oggi - timedelta(days=GIORNI)).strftime("%d/%m/%Y")
    end = oggi.strftime("%d/%m/%Y")
    size = 100
    dati = get("/avvisi-full-text", params_base(start, end, 0, size))
    if not dati:
        return []
    contenuto = dati.get("content", [])
    count = dati.get("count")
    print(f"Prima pagina: richiesti {size}, restituiti {len(contenuto)}, count dichiarato {count}")
    if contenuto and len(contenuto) < size and count and count > len(contenuto):
        size = len(contenuto)
        print(f"Il servizio sembra limitare la pagina a {size}: uso size={size} per tutta la raccolta.")

    visti = {}
    for a in contenuto:
        visti[a.get("idAvviso")] = a
    pagina = 1
    while pagina < MAX_PAGINE:
        dati = get("/avvisi-full-text", params_base(start, end, pagina, size))
        if not dati:
            print("   Raccolta interrotta (errore): i dati sono parziali.")
            break
        cont = dati.get("content", [])
        if not cont:
            break
        nuovi = 0
        for a in cont:
            if a.get("idAvviso") not in visti:
                visti[a.get("idAvviso")] = a
                nuovi += 1
        if pagina == 1 or pagina % 5 == 0:
            print(f"   pagina {pagina}: {len(cont)} risultati, {nuovi} nuovi (totale {len(visti)})")
        if nuovi == 0:
            print(f"   pagina {pagina}: nessun avviso nuovo, mi fermo.")
            break
        if len(cont) < size:
            break
        pagina += 1
    return list(visti.values())


def record(a: dict) -> dict:
    def tutti(k):
        return list(dict.fromkeys(str(x) for x in trova(a, k) if x not in (None, "")))

    valori = [x for x in trova(a, "valore_complessivo_stimato") if isinstance(x, (int, float))]
    return {
        "idAvviso": a.get("idAvviso"),
        "idAppalto": a.get("idAppalto"),
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


def riga(r, cat=""):
    luoghi = ", ".join(r["luoghi"][:3])
    return (f"{r['pubblicato']} | {cat[:1]} | {r['committente']} | {luoghi} "
            f"| scad {r['scadenza']} | {r['valore_max']}")


def main():
    t0 = time.time()
    toscani, province, regioni_per_nome = carica_comuni()
    print(f"Comuni toscani nell'elenco: {len(toscani)} | province: {sorted(province)}")

    print("\n=== Mappa tipologie (primi 2500 caratteri) ===")
    mappa = get("/map")
    if mappa:
        (OUT / "A_map.json").write_text(json.dumps(mappa, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(mappa, ensure_ascii=False)[:2500])

    print(f"\n=== Raccolta bandi degli ultimi {GIORNI} giorni (tutta Italia) ===")
    grezzi = raccogli()
    print(f"\nAvvisi raccolti: {len(grezzi)}")

    ultimi = {}
    versioni = Counter()
    for a in grezzi:
        r = record(a)
        chiave = r["idAppalto"] or r["idAvviso"]
        versioni[chiave] += 1
        if chiave not in ultimi or r["pubblicato"] >= ultimi[chiave]["pubblicato"]:
            ultimi[chiave] = r
    multi = sum(1 for v in versioni.values() if v > 1)
    print(f"Procedure uniche (per idAppalto): {len(ultimi)} | con piu' di una versione: {multi}")

    unici = list(ultimi.values())
    (OUT / "harvest_unici.json").write_text(json.dumps(unici, ensure_ascii=False, indent=1), encoding="utf-8")

    cat_di = {}
    for r in unici:
        cat_di[r["idAvviso"]] = classifica(r, toscani, province, regioni_per_nome)
    tosc = [r for r in unici if cat_di[r["idAvviso"]]]
    (OUT / "toscana.json").write_text(json.dumps(
        [dict(r, categoria=cat_di[r["idAvviso"]]) for r in tosc], ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n=== Classificazione Toscana ===")
    per_cat = Counter(cat_di[r["idAvviso"]] for r in tosc)
    for k in sorted(per_cat):
        print(f"  {k}: {per_cat[k]}")
    print(f"  Totale Toscana (A+B+C+D): {len(tosc)} su {len(unici)} procedure uniche")

    print("\n=== Per provincia (solo A, B, C) ===")
    per_prov = Counter(provincia_di(r, toscani, province)
                       for r in tosc if cat_di[r["idAvviso"]][0] in "ABC")
    for p, n in per_prov.most_common():
        print(f"  {p}: {n}")

    print("\n=== Per giorno di pubblicazione (Italia / Toscana) ===")
    tot_g = Counter(r["pubblicato"] for r in unici)
    tos_g = Counter(r["pubblicato"] for r in tosc)
    for g in sorted(tot_g):
        print(f"  {g}: {tot_g[g]} / {tos_g.get(g, 0)}")

    print("\n=== Empolese Valdelsa ===")
    ev = {norm(x) for x in EMPOLESE}
    n_ev = 0
    for r in sorted(unici, key=lambda x: x["pubblicato"], reverse=True):
        if any(norm(lu) in ev for lu in r["luoghi"]) or "EMPOLESE" in norm(r["committente"]):
            print("  " + riga(r, cat_di.get(r["idAvviso"]) or "-"))
            n_ev += 1
    print(f"  Totale: {n_ev}")

    print("\n=== Controllo qualita': luogo senza corrispondenza con provincia toscana confermata ===")
    sconosciuti = Counter()
    for r in unici:
        if cat_di[r["idAvviso"]] == "A_luogo_confermato":
            for lu in r["luoghi"]:
                if norm(lu) not in toscani:
                    sconosciuti[lu] += 1
    for lu, n in sconosciuti.most_common(15):
        print(f"  {lu}: {n}")
    if not sconosciuti:
        print("  nessuno")

    print("\n=== Prime 40 procedure toscane (piu' recenti) ===")
    for r in sorted(tosc, key=lambda x: x["pubblicato"], reverse=True)[:40]:
        print("  " + riga(r, cat_di[r["idAvviso"]]))

    print(f"\nTempo totale: {time.time() - t0:.0f} secondi")
    print("Fatto. Incolla qui in chat tutto l'output della console.")


if __name__ == "__main__":
    main()
