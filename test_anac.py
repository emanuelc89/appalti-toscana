"""
Step 1 - Test di fattibilità API ANAC (Piattaforma Pubblicità a Valore Legale).

Cosa fa (4 chiamate in tutto, ~1 al secondo, in sola lettura):
  A) /map                   -> mappa delle tipologie (per capire i codici scheda)
  B) /avvisi-full-text      -> ultimi 5 bandi degli ultimi 7 giorni
  C) /avvisi/{id}           -> dettaglio del primo bando (qui cerchiamo regione/comune)
  D) /avvisi-full-text      -> ricerca testuale "Comune di Empoli", ultimi 30 giorni

Salva tutto in JSON nella cartella 'anac_test' accanto allo script.
Requisiti: Python 3.10+, pip install requests
"""
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

BASE = "https://pubblicitalegale.anticorruzione.it/api/v0"
OUT = Path(__file__).parent / "anac_test"
OUT.mkdir(exist_ok=True)

session = requests.Session()
session.headers.update({
    "Accept": "application/json",
    "User-Agent": "AppaltiToscana-test/0.1 (progetto civico; contatto: emanuelc89 su GitHub)",
})

PAUSA = 1.2  # secondi tra una chiamata e l'altra (ANAC non dichiara quote: andiamo piano)


def fmt(d: date) -> str:
    return d.strftime("%d/%m/%Y")  # formato richiesto dall'API: GG/MM/AAAA


def chiama(nome: str, path: str, params: dict | None = None):
    url = BASE + path
    print(f"\n--- {nome}: GET {path} {params or ''}")
    try:
        r = session.get(url, params=params, timeout=30)
    except requests.RequestException as e:
        print(f"ERRORE di rete: {e}")
        return None
    print(f"Status: {r.status_code} | Content-Type: {r.headers.get('Content-Type')}")
    if r.status_code != 200:
        print("Corpo risposta (primi 400 caratteri):")
        print(r.text[:400])
        if r.status_code in (403, 406) or "Request Rejected" in r.text:
            print(">> Possibile blocco WAF: annota questo risultato, è un'informazione importante.")
        return None
    try:
        dati = r.json()
    except ValueError:
        print("La risposta non è JSON. Primi 400 caratteri:")
        print(r.text[:400])
        return None
    (OUT / f"{nome}.json").write_text(
        json.dumps(dati, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Salvato: anac_test/{nome}.json")
    time.sleep(PAUSA)
    return dati


def main():
    oggi = date.today()

    # A) mappa tipologie
    mappa = chiama("A_map", "/map")

    # B) ultimi bandi (codiceScheda=4 = bandi, secondo la documentazione della CLI di Borruso;
    #    verifica il mapping in A_map.json)
    ricerca = chiama("B_ultimi_bandi", "/avvisi-full-text", {
        "codiceScheda": "4",
        "dataPubblicazioneStart": fmt(oggi - timedelta(days=7)),
        "dataPubblicazioneEnd": fmt(oggi),
        "sortField": "dataPubblicazione",
        "sortDirection": "DESC",
        "page": 0,
        "size": 5,
    })

    primo_id = None
    if isinstance(ricerca, dict):
        contenuto = ricerca.get("content", [])
        print(f"Chiavi della risposta: {list(ricerca.keys())}")
        print(f"Avvisi restituiti: {len(contenuto)}")
        if contenuto:
            print("Campi del primo avviso:", list(contenuto[0].keys()))
            primo_id = contenuto[0].get("idAvviso")
    elif isinstance(ricerca, list) and ricerca:
        primo_id = ricerca[0].get("idAvviso")

    # C) dettaglio del primo avviso
    if primo_id:
        dettaglio = chiama("C_dettaglio_primo", f"/avvisi/{primo_id}")
        if isinstance(dettaglio, dict):
            print("Chiavi principali del dettaglio:", list(dettaglio.keys()))
    else:
        print("\nNessun idAvviso da cui partire: salto il dettaglio.")

    # D) ricerca testuale sul territorio
    chiama("D_empoli_30gg", "/avvisi-full-text", {
        "keywords": "Comune di Empoli",
        "codiceScheda": "4",
        "dataPubblicazioneStart": fmt(oggi - timedelta(days=30)),
        "dataPubblicazioneEnd": fmt(oggi),
        "page": 0,
        "size": 5,
    })

    print("\nFatto. Incolla qui in chat l'output della console e allega (o incolla) "
          "C_dettaglio_primo.json.")


if __name__ == "__main__":
    sys.exit(main())