"""
Step 4 - Sonda: struttura dei campi e volumi per bandi (titolo vuoto), 5b, esiti 7 e 8a.

Cosa fa (circa 10 chiamate, pochi secondi):
  1) conta quanti avvisi ci sono negli ultimi 14 giorni per i template 4, 5b, 7, 8a, 9 (richiesta con size=1)
  2) per 4 template prende un avviso d'esempio e ne stampa i PERCORSI dei campi con un valore di esempio
     (i valori dei campi che riguardano aggiudicatari/operatori/codici fiscali sono MASCHERATI)
  3) salva l'avviso grezzo in anac_test/sonda_<template>.json (con i dati personali non mascherati: non condividerli)

Requisiti: Python 3.10+, requests.
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

MASCHERA = ("aggiudicatar", "operator", "cognome", "codice_fiscale", "partita_iva", "piva")


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


def finestra(giorni):
    oggi = date.today()
    return ((oggi - timedelta(days=giorni)).strftime("%d/%m/%Y"), oggi.strftime("%d/%m/%Y"))


def ricerca(template, giorni, size):
    start, end = finestra(giorni)
    return get("/avvisi-full-text", {
        "codiceScheda": template,
        "dataPubblicazioneStart": start,
        "dataPubblicazioneEnd": end,
        "sortField": "dataPubblicazione",
        "sortDirection": "DESC",
        "size": size,
    })


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


def percorsi(obj, prefisso="", out=None):
    """Percorsi foglia con un valore d'esempio; gli elenchi diventano [] o [nome sezione]."""
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


def campione(valore, percorso):
    if valore is None or valore == "":
        return repr(valore)
    if any(m in percorso.lower() for m in MASCHERA):
        return "<mascherato>"
    return repr(valore)[:70]


def main():
    print("=== Conteggi ultimi 14 giorni (count dichiarato) ===")
    for template, nome in [("4", "Bandi"), ("5b", "Indagini di mercato sotto soglia"),
                           ("7", "Esiti (risultati)"), ("8a", "Affidamenti diretti sotto soglia"),
                           ("9", "Preavvisi di aggiudicazione diretta")]:
        d = ricerca(template, 14, 1)
        print(f"  {template:>2} {nome}: {d.get('count') if d else 'errore'}")

    print("\n=== Struttura dei campi (avvisi d'esempio, ultimi 3 giorni) ===")
    esempi = [("4", "Bando con titolo vuoto (se esiste)"), ("5b", "Indagine di mercato sotto soglia"),
              ("7", "Esito (risultati)"), ("8a", "Affidamento diretto sotto soglia")]
    for template, nome in esempi:
        print(f"\n--- Template {template}: {nome} ---")
        d = ricerca(template, 3, 30)
        cont = (d or {}).get("content") or []
        if not cont:
            print("   nessun avviso trovato")
            continue
        scelto = cont[0]
        if template == "4":
            for a in cont:
                if not trova(a, "titolo"):
                    scelto = a
                    break
        (OUT / f"sonda_{template}.json").write_text(json.dumps(scelto, ensure_ascii=False, indent=2),
                                                    encoding="utf-8")
        print(f"   idAvviso {scelto.get('idAvviso')} | codiceScheda {scelto.get('codiceScheda')} "
              f"| titolo presente: {bool(trova(scelto, 'titolo'))}")
        for p, v in percorsi(scelto).items():
            print(f"   {p} = {campione(v, p)}")

    print("\nFatto. Incolla qui in chat tutto l'output della console.")


if __name__ == "__main__":
    main()
