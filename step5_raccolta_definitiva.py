"""
Step 5 - Raccolta definitiva: dati puliti per Toscana, pronti per il sito.

Tipologie raccolte (finestra in giorni):
   4   bandi                          (14)
   5b  indagini di mercato sotto soglia (14)
   5a  indagini di mercato sopra soglia (14)
   2   pre-informazione indittivi      (14)
   7   esiti                           (3)
   8a  affidamenti diretti sotto soglia (3)

Categorie geografiche (campo 'geo'):
   A  provincia toscana indicata nell'avviso (luogo confermato)
   B  provincia assente, comune con nome univoco toscano
   C  provincia assente, nome di comune toscano ma esiste anche in altre regioni (ambiguo)
   D  nessun luogo indicato, committente con 'TOSCAN' nel nome (debole)
   M  avviso multiregionale (3 o piu' luoghi non toscani): escluso dalle pagine per comune

Privacy: i nomi degli aggiudicatari sono tenuti SOLO se il codice fiscale ha 11 cifre (societa');
         i codici fiscali non vengono mai salvati.

Output: cartella 'dati' accanto allo script:
   dati/avvisi_toscana.json   elenco dei record toscani (incluse le categorie D e M)
   dati/meta.json             data di generazione, finestre, conteggi
Cache: anac_test/cpv_cache.json (CPV dai dettagli, per non riscaricarli)

Requisiti: Python 3.10+, requests. Durata prevista: 4-6 minuti (richieste distanziate di 1,2 s).
"""
import html
import json
import re
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

BASE = "https://pubblicitalegale.anticorruzione.it/api/v0"
AQUI = Path(__file__).parent
OUT = AQUI / "anac_test"
DATI = AQUI / "dati"
OUT.mkdir(exist_ok=True)
DATI.mkdir(exist_ok=True)

PAUSA = 1.2
SIZE = 200
MAX_PAGINE = 150
COMUNI_FILE = OUT / "comuni.json"
COMUNI_URL = "https://raw.githubusercontent.com/matteocontrini/comuni-json/master/comuni.json"
CPV_CACHE = OUT / "cpv_cache.json"
FETCH_CPV = True

TEMPLATES = [
    ("4", "bando", 14),
    ("5b", "indagine_sotto_soglia", 14),
    ("5a", "indagine_sopra_soglia", 14),
    ("2", "preinformazione", 14),
    ("7", "esito", 3),
    ("8a", "affidamento_diretto", 3),
]
TIPI_CON_CPV = ("bando", "indagine_sotto_soglia", "indagine_sopra_soglia")

EMPOLESE = ["Capraia e Limite", "Castelfiorentino", "Cerreto Guidi", "Certaldo", "Empoli",
            "Fucecchio", "Gambassi Terme", "Montaione", "Montelupo Fiorentino",
            "Montespertoli", "Vinci"]

MASCHERA = ("aggiudicatar", "operator", "cognome", "codice_fiscale", "partita_iva", "piva")

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


def pulisci(s):
    if s is None:
        return None
    s = html.unescape(str(s))
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def trova(obj, chiave, trovati=None):
    """Valori scalari di una chiave, in tutto il JSON annidato."""
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
    """Valori di qualsiasi tipo di una chiave (senza scendere dentro la chiave trovata)."""
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
    for c in comuni:
        n = norm(c["nome"])
        reg = c["regione"]["nome"]
        regioni_per_nome[n].add(reg)
        if reg == "Toscana":
            toscani[n] = c["provincia"]["nome"]
            prov_canon[norm(c["provincia"]["nome"])] = c["provincia"]["nome"]
    return toscani, prov_canon, regioni_per_nome


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
            print("   Raccolta interrotta (errore): dati parziali.")
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


# ---------- estrazione ----------

def estrai(a: dict, template: str, tipo: str) -> dict:
    valori = []
    for k in ("valore_complessivo_stimato", "valore_affidamento"):
        valori += [x for x in trova(a, k) if isinstance(x, (int, float))]

    agg, nascosti = [], 0
    for elenco in valori_chiave(a, "aggiudicatari_ad"):
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
    cpv = [{"codice": None, "descrizione": pulisci(x)} for x in lista(a, "cpv")]
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
        "cig": lista(a, "cig"),
        "cpv": cpv,
        "valore": max(valori) if valori else None,
        "luoghi": lista(a, "luogo_istat"),
        "nuts": lista(a, "luogo_nuts"),
        "link_documenti": primo(a, "documenti_di_gara_link"),
        "link_ted": primo(a, "link_eform_ted"),
        "aggiudicatari": agg,
        "aggiudicatari_nascosti": nascosti,
        "stato_anac": a.get("stato"),
    }


def classifica(r, toscani, prov_canon, regioni_per_nome):
    nuts_n = [norm(x) for x in r["nuts"]]
    luoghi_n = [norm(x) for x in r["luoghi"]]
    non_tosc = [lu for lu in luoghi_n if lu not in toscani]
    if any(n in prov_canon for n in nuts_n):
        cat = "A"
    elif not nuts_n and any(lu in toscani for lu in luoghi_n):
        comuni_t = [lu for lu in luoghi_n if lu in toscani]
        cat = "C" if any(len(regioni_per_nome[lu]) > 1 for lu in comuni_t) else "B"
    elif not luoghi_n and not nuts_n and "TOSCAN" in norm(r["committente"]):
        cat = "D"
    else:
        return None
    if cat in "ABC" and len(non_tosc) >= 3:
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
    print(f"\nDettagli da scaricare per i CPV: {len(da_fare)} (gia' in cache: {len(cache)})")
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


# ---------- stampa ----------

def percorsi(obj, prefisso="", out=None):
    if out is None:
        out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "scoreDetails":
                continue
            percorsi(v, f"{prefisso}.{k}" if prefisso else k, out)
    elif isinstance(obj, list):
        for x in obj:
            etichetta = "[]"
            if isinstance(x, dict) and isinstance(x.get("name"), str):
                etichetta = f"[{x['name'][:28]}]"
            percorsi(x, prefisso + etichetta, out)
    else:
        if prefisso not in out or (out[prefisso] is None and obj is not None):
            out[prefisso] = obj
    return out


def riga(r):
    luoghi = ", ".join(r["luoghi"][:3])
    if len(r["luoghi"]) > 3:
        luoghi += f" (+{len(r['luoghi']) - 3})"
    agg = "; ".join(f"{x['denominazione']} {x['importo']}" for x in r["aggiudicatari"][:2])
    return (f"{r['pubblicato']} | {r['geo']} | {r['committente']} | {luoghi} | {r['valore']} "
            f"| {(r['titolo'] or '')[:70]}" + (f" | AGG: {agg}" if agg else ""))


def main():
    t0 = time.time()
    toscani, prov_canon, regioni_per_nome = carica_comuni()
    print(f"Comuni toscani: {len(toscani)} | province: {sorted(prov_canon.values())}")

    oggi = date.today()
    tutti = []
    stat = {}
    grezzi_esiti = {}
    for template, tipo, giorni in TEMPLATES:
        start = (oggi - timedelta(days=giorni)).strftime("%d/%m/%Y")
        end = oggi.strftime("%d/%m/%Y")
        print(f"\n=== Template {template} ({tipo}) {start} - {end} ===")
        grezzi = raccogli(template, start, end)
        ultimi = {}
        versioni = Counter()
        for a in grezzi:
            r = estrai(a, template, tipo)
            chiave = r["procedura"] or r["id"]
            versioni[chiave] += 1
            if chiave not in ultimi or r["pubblicato"] >= ultimi[chiave]["pubblicato"]:
                ultimi[chiave] = r
                if template == "7":
                    grezzi_esiti[r["id"]] = a
        for chiave, r in ultimi.items():
            r["versioni"] = versioni[chiave]
            r["geo"] = classifica(r, toscani, prov_canon, regioni_per_nome)
            r["province"] = province_record(r, toscani, prov_canon) if r["geo"] else []
            r["comuni_toscani"] = [lu for lu in r["luoghi"] if norm(lu) in toscani] if r["geo"] else []
        unici = list(ultimi.values())
        stat[template] = {"raccolti": len(grezzi), "uniche": len(unici), "giorni": giorni,
                          "cat": Counter(r["geo"] for r in unici if r["geo"])}
        tutti.extend(unici)

    tosc = [r for r in tutti if r["geo"]]
    if FETCH_CPV:
        arricchisci_cpv(tosc)

    meta = {
        "generato": datetime.now().isoformat(timespec="seconds"),
        "fonte": "ANAC - Piattaforma di Pubblicita' a Valore Legale (pubblicitalegale.anticorruzione.it)",
        "finestre_giorni": {t: g for t, _, g in TEMPLATES},
        "conteggi": {t: {"raccolti": s["raccolti"], "uniche": s["uniche"],
                         "toscana": dict(s["cat"])} for t, s in stat.items()},
    }
    (DATI / "avvisi_toscana.json").write_text(json.dumps(tosc, ensure_ascii=False, indent=1), encoding="utf-8")
    (DATI / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n=== RIEPILOGO per tipologia ===")
    for template, tipo, giorni in TEMPLATES:
        s = stat[template]
        dett = ", ".join(f"{k}={v}" for k, v in sorted(s["cat"].items())) or "nessuna"
        print(f"  {template:>2} {tipo} ({giorni}gg): avvisi {s['raccolti']} | uniche {s['uniche']} | Toscana [{dett}]")

    print("\n=== Codici scheda piu' frequenti (esiti e affidamenti) ===")
    for t in ("7", "8a"):
        c = Counter(r["scheda"] for r in tutti if r["template"] == t)
        print(f"  {t}: {c.most_common(8)}")

    def per_provincia(titolo, filtro):
        print(f"\n=== Per provincia: {titolo} (solo A, B, C) ===")
        c = Counter()
        for r in tosc:
            if filtro(r) and r["geo"] in ("A", "B", "C") and r["province"]:
                c[r["province"][0]] += 1
        for p, n in c.most_common():
            print(f"  {p}: {n}")

    per_provincia("bandi e indagini (14 gg)", lambda r: r["template"] in ("4", "5b", "5a"))
    per_provincia("affidamenti diretti (3 gg)", lambda r: r["template"] == "8a")

    print("\n=== Copertura comuni toscani (su 273) ===")
    cop_bi = {norm(c) for r in tosc if r["template"] in ("4", "5b", "5a") and r["geo"] in ("A", "B", "C")
              for c in r["comuni_toscani"]}
    cop_ad = {norm(c) for r in tosc if r["template"] == "8a" and r["geo"] in ("A", "B", "C")
              for c in r["comuni_toscani"]}
    print(f"  bandi/indagini 14gg: {len(cop_bi)} comuni | affidamenti diretti 3gg: {len(cop_ad)} comuni")

    print("\n=== Empolese Valdelsa ===")
    ev = {norm(x) for x in EMPOLESE}
    per_tipo = Counter()
    righe_ev = []
    for r in sorted(tutti, key=lambda x: x["pubblicato"], reverse=True):
        if any(norm(lu) in ev for lu in r["luoghi"]) or "EMPOLESE" in norm(r["committente"]):
            per_tipo[r["tipo"]] += 1
            righe_ev.append(r)
    print("  " + ", ".join(f"{k}={v}" for k, v in per_tipo.items()))
    for r in righe_ev[:30]:
        print("  " + riga(r))

    print("\n=== Top 10 committenti toscani negli affidamenti diretti ===")
    c = Counter(r["committente"] for r in tosc if r["template"] == "8a" and r["geo"] in ("A", "B", "C"))
    for nome, n in c.most_common(10):
        print(f"  {n:>3}  {nome}")

    print("\n=== Controlli di qualita' ===")
    print(f"  titolo mancante: {sum(1 for r in tosc if not r['titolo'])} su {len(tosc)}")
    print(f"  anomalia scadenza < pubblicazione: {sum(1 for r in tosc if r['anomalia_scadenza'])}")
    print(f"  multiregionali (M): {sum(1 for r in tosc if r['geo'] == 'M')}")
    for t in ("4", "5b", "7", "8a"):
        n_c = sum(1 for r in tosc if r["template"] == t and r["geo"] == "C")
        n_tot = sum(1 for r in tosc if r["template"] == t)
        print(f"  template {t}: ambigui (C) {n_c} su {n_tot} toscani")
    n_nasc = sum(r["aggiudicatari_nascosti"] for r in tosc if r["template"] == "8a")
    n_vis = sum(len(r["aggiudicatari"]) for r in tosc if r["template"] == "8a")
    print(f"  affidamenti diretti: aggiudicatari pubblicabili (societa') {n_vis} | nascosti {n_nasc}")
    print(f"  CPV con codice (bandi/indagini toscani): "
          f"{sum(1 for r in tosc if r['tipo'] in TIPI_CON_CPV and any(x['codice'] for x in r['cpv']))} su "
          f"{sum(1 for r in tosc if r['tipo'] in TIPI_CON_CPV)}")

    print("\n=== Esiti (7): campi di un vero esito di aggiudicazione (scheda diversa da NAG) ===")
    esempio = None
    for r in tosc:
        if r["template"] == "7" and r["scheda"] != "NAG" and r["id"] in grezzi_esiti:
            esempio = r
            break
    if not esempio:
        for r in tutti:
            if r["template"] == "7" and r["scheda"] != "NAG" and r["id"] in grezzi_esiti:
                esempio = r
                break
    if esempio:
        print(f"  scheda {esempio['scheda']} | {esempio['committente']}")
        chiavi = ("aggiudic", "importo", "ribasso", "soggett", "offert", "valore", "luogo")
        for p, v in percorsi(grezzi_esiti[esempio["id"]]).items():
            if any(k in p.lower() for k in chiavi):
                mostra = "<mascherato>" if any(m in p.lower() for m in MASCHERA) else repr(v)[:70]
                print(f"  {p} = {mostra}")
    else:
        print("  nessun esito con scheda diversa da NAG nella finestra")

    print(f"\nFile scritti: dati/avvisi_toscana.json ({len(tosc)} record), dati/meta.json")
    print(f"Tempo totale: {time.time() - t0:.0f} secondi")
    print("Fatto. Incolla qui in chat tutto l'output della console.")


if __name__ == "__main__":
    main()
