"""
Step 2 - Come filtriamo la Toscana?

Parte 1 (locale, nessuna chiamata): analizza B_ultimi_bandi.json e D_empoli_30gg.json
    già salvati dallo step 1, per capire se i risultati di ricerca contengono già
    committente e luogo (campo 'templates') oppure serve il dettaglio per ogni avviso.

Parte 2 (rete, ~8 chiamate): 3 ricerche testuali (Toscana, Firenze, Empoli) sugli ultimi
    14 giorni; per i risultati senza dati completi apre al massimo 5 dettagli.

Requisiti: Python 3.10+, requests. Mettilo nella stessa cartella di test_anac.py.
"""
import json
import time
from datetime import date, timedelta
from pathlib import Path

import requests

BASE = "https://pubblicitalegale.anticorruzione.it/api/v0"
OUT = Path(__file__).parent / "anac_test"
OUT.mkdir(exist_ok=True)
PAUSA = 1.2

session = requests.Session()
session.headers.update({
    "Accept": "application/json",
    "User-Agent": "AppaltiToscana-test/0.1 (progetto civico; contatto: emanuelc89 su GitHub)",
})


def trova(obj, chiave, trovati=None):
    """Cerca ricorsivamente tutti i valori scalari di una chiave in un JSON annidato."""
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


def riassumi(avviso: dict) -> dict:
    def primo(chiave):
        t = trova(avviso, chiave)
        return t[0] if t else None

    return {
        "pubblicato": (avviso.get("dataPubblicazione") or "")[:10],
        "committente": primo("denominazione_amministrazione"),
        "luogo_nuts": primo("luogo_nuts"),
        "luogo_istat": primo("luogo_istat"),
        "cig": primo("cig"),
        "valore": primo("valore_complessivo_stimato"),
    }


def stampa(r: dict):
    print(f"   {r['pubblicato']} | {r['committente']} | nuts={r['luogo_nuts']} "
          f"| istat={r['luogo_istat']} | CIG={r['cig']} | valore={r['valore']}")


def get(path, params=None):
    try:
        r = session.get(BASE + path, params=params, timeout=30)
    except requests.RequestException as e:
        print(f"   ERRORE di rete: {e}")
        return None
    if r.status_code != 200:
        print(f"   Status {r.status_code}: {r.text[:300]}")
        return None
    time.sleep(PAUSA)
    return r.json()


def parte1():
    print("=== PARTE 1: analisi locale dei file già salvati ===")
    for nome in ("B_ultimi_bandi.json", "D_empoli_30gg.json"):
        f = OUT / nome
        if not f.exists():
            print(f"\n{nome}: non trovato, salto.")
            continue
        dati = json.loads(f.read_text(encoding="utf-8"))
        contenuto = dati.get("content", [])
        print(f"\n{nome}: count dichiarato = {dati.get('count')} | restituiti = {len(contenuto)}")
        for a in contenuto:
            ha_templates = bool(a.get("templates"))
            print(f" - idAvviso {a.get('idAvviso')} | 'templates' presente: {ha_templates}")
            if ha_templates:
                stampa(riassumi(a))


def cerca(keywords: str, giorni: int = 14, size: int = 20):
    oggi = date.today()
    params = {
        "keywords": keywords,
        "codiceScheda": "4",
        "dataPubblicazioneStart": (oggi - timedelta(days=giorni)).strftime("%d/%m/%Y"),
        "dataPubblicazioneEnd": oggi.strftime("%d/%m/%Y"),
        "page": 0,
        "size": size,
    }
    print(f"\n--- Ricerca '{keywords}' (ultimi {giorni} giorni, bandi)")
    dati = get("/avvisi-full-text", params)
    if not dati:
        return
    nome_file = f"E_{keywords.replace(' ', '_')}.json"
    (OUT / nome_file).write_text(json.dumps(dati, ensure_ascii=False, indent=2), encoding="utf-8")
    contenuto = dati.get("content", [])
    print(f"   count dichiarato (stima) = {dati.get('count')} | restituiti = {len(contenuto)}")

    senza_dati = 0
    for a in contenuto:
        r = riassumi(a)
        if r["committente"]:
            stampa(r)
        else:
            senza_dati += 1
    if senza_dati:
        print(f"   {senza_dati} risultati senza committente nella ricerca: apro al massimo 5 dettagli")
        aperti = 0
        for a in contenuto:
            if aperti >= 5:
                break
            if not riassumi(a)["committente"]:
                det = get(f"/avvisi/{a.get('idAvviso')}")
                aperti += 1
                if det:
                    stampa(riassumi(det))


def main():
    parte1()
    print("\n=== PARTE 2: ricerche testuali sul territorio ===")
    for kw in ("Toscana", "Firenze", "Empoli"):
        cerca(kw)
    print("\nFatto. Incolla qui in chat tutto l'output della console.")


if __name__ == "__main__":
    main()