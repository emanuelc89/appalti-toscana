# Appalti Toscana

Osservatorio aperto degli appalti pubblici in Toscana: bandi, indagini di mercato, esiti e affidamenti diretti,
raccolti ogni giorno dalla Piattaforma di Pubblicità a Valore Legale di ANAC e ordinati per comune.

> Progetto civico indipendente. Non è affiliato né approvato da ANAC o da alcuna stazione appaltante.

## Cosa contiene

I dati sono in `docs/dati/` (serviti da GitHub Pages):

| File | Contenuto |
|------|-----------|
| `recenti.json` | Opportunità (bandi, indagini di mercato, pre-informazioni) degli ultimi 60 giorni |
| `archivio/AAAA-MM-GG.json` | Tutti i record toscani pubblicati quel giorno (compresi esiti e affidamenti diretti) |
| `comuni.json` | I comuni toscani con slug e provincia |
| `meta.json` | Data di generazione, finestra e conteggi dell'ultimo aggiornamento |

Ogni record riporta, tra l'altro: tipo di avviso, committente, titolo, CIG, CPV, valore, luoghi, comuni toscani,
link ai documenti di gara e `url_anac` (la pagina ufficiale dell'avviso).

## Fonte e avvertenze

- **Fonte:** ANAC, Piattaforma di Pubblicità a Valore Legale (<https://pubblicitalegale.anticorruzione.it>).
- **Fa fede solo la pubblicazione ufficiale.** I dati sono quelli comunicati dalle stazioni appaltanti, che ne sono
  responsabili; ANAC li espone con stato `NON_VERIFICATO`. Per ogni avviso usa il link alla fonte ufficiale.
- **Il filtro geografico è ricostruito** dal luogo di esecuzione indicato nell'avviso (provincia e comune), non dalla
  sede dell'ente. Può contenere errori di inserimento a monte.
- **Importi degli aggiudicatari:** quando più operatori hanno lo stesso importo si tratta dell'importo del
  raggruppamento, non di una somma.
- **Il ribasso è indicativo:** si calcola solo per gli avvisi con un singolo lotto, confrontando valore stimato e
  offerta vincente.

## Privacy

I nomi degli aggiudicatari sono pubblicati solo quando il codice fiscale ha 11 cifre (società). Gli operatori con
codice fiscale di persona fisica (comprese molte ditte individuali) non vengono riportati e i codici fiscali non
vengono mai salvati.

## Come funziona

`aggiorna.py` interroga l'API pubblica di ANAC (paginazione a token, una richiesta ogni 1,2 secondi), deduplica per
procedura, classifica il territorio e aggiorna l'archivio. Un workflow GitHub Actions lo esegue nei giorni feriali.

```
python aggiorna.py                # ultimi 3 giorni
python aggiorna.py --giorni 14    # primo popolamento
```

Requisiti: Python 3.10+ e `requests`.

## Licenza

*Da decidere prima della pubblicazione.* Proposta: codice con licenza AGPL-3.0, dati derivati con CC BY-SA 4.0,
con attribuzione ad ANAC come fonte.
