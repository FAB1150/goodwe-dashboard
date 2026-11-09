# Portale GoodWe

Pagina web self-hosted per leggere e modificare le impostazioni di un inverter ibrido
**GoodWe ES/EM** (provato sul GW5048-EM) al posto della pagina impostazioni di SolarGo.

- Parla con il modulo WiFi in **UDP sulla porta 8899** con gli stessi comandi di SolarGo
  (dettagli in [docs/PROTOCOLLO.md](docs/PROTOCOLLO.md)).
- **Nessuna interrogazione continua**: legge solo quando apri la pagina o premi
  «Leggi dall'inverter», e scrive solo quando confermi un salvataggio.
- Dopo ogni scrittura **rilegge** il valore e indica se l'inverter l'ha preso; ogni modifica
  finisce nel **registro** (quando, cosa, prima, richiesto, riletto, esito).
- Con la **prova a secco** vedi i frame esatti che verrebbero inviati, senza inviarli.
- I **parametri di protezione di interfaccia rete (CEI 0-21)** si possono solo leggere: il
  portale non contiene comandi per scriverli.

## Cosa si può modificare

| Sezione | Impostazioni |
|---|---|
| Modalità di lavoro | Generale, Backup, Eco (off-grid solo visualizzata) |
| Fasce eco | 4 fasce: orari, carica/scarica, potenza %, giorni, attiva/disattiva |
| Batteria | Protezione SoC, DoD in rete, DoD fuori rete |
| Rete | Limite di immissione (W) |

Tutto il resto viene mostrato in sola lettura.

## Installazione con Portainer (Raspberry Pi 4)

1. Portainer → **Stacks** → **Add stack** → **Repository**.
2. Repository URL: `https://github.com/FAB1150/goodwe-dashboard`, reference: il branch da
   usare (per ora `refs/heads/claude/portale-goodwe`), compose path: `docker-compose.yml`.
3. **Deploy the stack**. La prima volta l'immagine viene costruita sul Raspberry (un paio di
   minuti).
4. Apri `http://<ip-del-raspberry>:8080`, inserisci l'IP dell'inverter (es. `10.10.30.149`) e
   premi **Salva IP**. L'IP resta salvato nel volume `portale-goodwe-dati`.

Il Raspberry deve poter raggiungere l'inverter in UDP sulla porta 8899 (se l'inverter è in
un'altra VLAN, il firewall deve permettere UDP 8899 in uscita dal Raspberry e le risposte).
Con la rete bridge di Docker di solito basta così; se le risposte non arrivano, prova
`network_mode: host` nel compose (togliendo la sezione `ports`).

### Variabili d'ambiente

| Variabile | Predefinito | Significato |
|---|---|---|
| `WRITES_ENABLED` | `false` | Con `false` il portale non invia **nessuna** scrittura: tutto finisce in prova a secco. |
| `INVERTER_HOST` | vuoto | IP iniziale (si può cambiare dalla pagina). |
| `PORTAL_USER` / `PORTAL_PASSWORD` | `admin` / vuoto | Se la password è impostata, la pagina chiede un login (HTTP Basic). Utile se un giorno lo metti dietro nginx. |

## Come procedere con le prove

1. **Fase 1, sola lettura** (`WRITES_ENABLED=false`): apri la pagina e confronta i valori con
   SolarGo e con Home Assistant. Nella sezione *Diagnostica* trovi i frame scambiati.
2. **Fase 2, scritture**: imposta `WRITES_ENABLED=true`, aggiorna lo stack e prova una
   impostazione alla volta, partendo da quelle meno delicate (limite di immissione, poi fasce
   eco, poi protezione SoC e DoD). Lascia attiva la **prova a secco** per vedere prima i frame,
   poi toglila per inviare. Ogni invio chiede conferma.

## Sviluppo

```bash
pip install -r requirements-dev.txt
pytest                                  # test di framing e API (con un inverter finto)
python -m tests.fake_inverter 18899     # inverter finto su 127.0.0.1:18899
DATA_DIR=./data uvicorn app.main:app --port 8080
```

I test usano risposte registrate da inverter ES/EM reali, prese dai test della libreria
[`goodwe`](https://github.com/marcelblijleven/goodwe) (licenza MIT).
