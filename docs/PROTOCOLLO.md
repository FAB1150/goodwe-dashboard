# Protocollo GoodWe ES/EM usato da SolarGo

Analisi di SolarGo (APK fornito da Fabio, decompilato con jadx) fatta **solo per
l'interoperabilità** con un GW5048-EM (famiglia ES, "piattaforma 105"), firmware
`0909A / 410-04000-11`. I riferimenti tra parentesi sono a classi di SolarGo.

## Trasporto

- UDP, porta **8899** del modulo WiFi; SolarGo apre un socket per richiesta
  (`GoodUDP.writeSync`), timeout **3 s**, **3 tentativi** (`UdpUnicast.sendForData`).
- Il protocollo DTLS che compare nell'app riguarda solo i moduli recenti che lo dichiarano;
  per l'ES si usa UDP in chiaro.
- La ricerca automatica (broadcast sulla porta 48899) non viene usata dal portale: l'IP si
  inserisce a mano (l'inverter sta in un'altra VLAN).

## Password

- La password installatore delle impostazioni viene confrontata **solo dentro l'app**
  (`LoginSettingActivity`, `InstallerInputPwdActivity`) e non viene mai inviata all'inverter.
  Il portale non la usa e non la mostra.
- C'è un login verso il dispositivo (`checkLoginPwd`, comando `AA55 C07F 0201 10 …`) ma
  SolarGo lo usa solo se il numero di serie lo dichiara (`Constant.SECURITY_AVAILABLE`, inverter
  recenti); per il tuo ES non viene usato.

## Formato AA55

```
richiesta: AA 55 <src> 7F <ctrl> <func> <len> <dati…> <somma_hi> <somma_lo>
risposta:  AA 55 7F <src> <ctrl> <func|80> <len> <dati…> <somma_hi> <somma_lo>
```

La somma è la somma semplice a 16 bit di tutti i byte precedenti. Un comando di scrittura
è riuscito se la risposta contiene un solo byte `06` (ACK); `15` è un rifiuto.

**Indirizzo sorgente: è la differenza principale rispetto a Home Assistant.**

| Sorgente | Usata da SolarGo per | Usata dalla libreria `goodwe` (HA) |
|---|---|---|
| `C0` | comandi "classici" `01xx` / `03xx` (`Constant.MONITOR_ADDRESS`) | **tutto** |
| `B0` | lettura registri `011A`, scrittura registri `0239` (`build105SettingPackage`, `build105ReadingPackage`) | mai |

## Letture eseguite dal portale (all'apertura o con «Leggi dall'inverter»)

| Cosa | Frame | Note |
|---|---|---|
| Informazioni dispositivo | `AA55 C07F 0102 00` | risposta `0182`: firmware [0:5], modello [5:15], SN [31:47], software [51:63], paese di sicurezza [63], versione ARM estesa [64:76] |
| Impostazioni | `AA55 C07F 0109 00` | risposta `0189`, tabella sotto (`DataCollectUtil.getSettingData1`) |
| Fasce eco | `AA55 B07F 011A 03 0701 10` | 16 registri, 4 gruppi da 8 byte (`READ_BATTERY_MODE_LIST`) |
| DoD fuori rete | `AA55 B07F 011A 03 05A1 01` | valore = 100 − DoD (`READ_OFFLINE_DOD`) |
| Protezioni rete (solo su richiesta) | Modbus `F7 03 B158 0011` e `F7 03 B169 0010` | `CustomSaftyParamEnum`, **mai scritti** |

### Blocco impostazioni `0189` (offset in byte)

| Offset | Contenuto |
|---|---|
| 0–3 | finestra di carica legacy (h, m, h, m); 4–5 limite % |
| 6–9 | finestra di scarica legacy; 10–11 limite % |
| 13 | uscita backup (0/1) |
| 15 | carica da rete in off-grid |
| 17 | scansione ombre |
| 18–19 | limitazione immissione (1 = attiva) |
| 22–23 | capacità (Ah) |
| 24–25 | tensione di carica (/10 V) |
| 26–27 | corrente di carica (SolarGo: grezzo / 0,266 / 10 A) |
| 28–29 | corrente di scarica (SolarGo: grezzo / 0,128 / 10 A, max 120) |
| 30–31 | tensione di scarica (/10 V) |
| 32–33 | **SoC minimo in rete = 100 − DoD** |
| 34–35 | attivazione batteria |
| 42–43 | fattore di potenza |
| 44–51 | parametri di mantenimento, modello batteria (255 = personalizzata) |
| 52–53 | limite di immissione (W) |
| 56–57 | **protezione SoC: 1 = disattivata**, altro = attiva |
| 60–63 | carica di equalizzazione |
| 66–67 | modalità di lavoro: 0 generale, 1 off-grid, 2 backup, 3 eco |
| 68–69 | controllo qualità rete |

### Fasce eco (registri `0701`–`0710`)

Ogni gruppo è lungo 8 byte: `hh_inizio mm_inizio hh_fine mm_fine potenza(int16 con segno) attivo giorni`

- potenza in % della nominale: **negativa = carica**, positiva = scarica (`BatteryModeActivity`);
- attivo: `FF` sì, `00` no;
- giorni: bit 0 = domenica … bit 6 = sabato;
- niente SoC obiettivo su questo firmware (arriva con ARM ≥ 14, "nuova modalità eco", non
  disponibile per il tuo inverter);
- SolarGo rifiuta inizio ≥ fine e controlla le sovrapposizioni (modalità opposte o potenze diverse).

## Scritture (come SolarGo)

| Impostazione | Sequenza | Origine in SolarGo |
|---|---|---|
| Fascia eco *n* | `C07F 0359 01 03` (modalità eco), poi `B07F 0239 0B <0701+4(n−1)> 04 <8 byte>` | `BatteryModeActivity` → `setBatteryModeParam` |
| Attiva/disattiva fascia *n* | `C07F 0359 01 03`, poi `B07F 0239 05 <0704+4(n−1)> 01 <FF/00> <giorni>` | `BatteryModeListActivity` → `setBatteryModeSwitch` |
| Protezione SoC | `C07F 033B 03 00 <00 attiva / 01 disattiva> 00` | `setSocProtect` |
| DoD in rete | `B07F 0239 05 0560 01 <00> <100−DoD>` | `BatteryFunction105Activity.setBatteryDod` |
| DoD fuori rete | `B07F 0239 05 05A1 01 <00> <100−DoD>` | `setOfflineDod` |
| Limite di immissione | `C07F 0335 02 <W hi> <W lo>` | `buildPackage(66)` |
| Modalità generale | `032C 05 00 00 00 00 00`, `032D 05 00 00 00 00 00`, `B07F 0239 05 0700 01 0001`, `0359 01 00`, `0336 01 00` | `ChooseWorkModeActivity`, ARM ≥ 7 |
| Modalità backup | `B07F 0239 05 0700 01 0001`, `0336 01 00`, `032C 05 00 00 17 3B 0A`, `0359 01 02` | `initESEMBackupModeDialog` |
| Modalità eco | `0359 01 03` (poi si gestiscono le fasce) | |

### Perché le scritture della DoD da Home Assistant non hanno effetto

1. **Indirizzo sorgente.** HA scrive lo stesso registro (`0x0560`) con lo stesso valore, ma
   da `C0` (`aa55 c07f 0239 05 0560 01 0014 02f8`); SolarGo da `B0`
   (`aa55 b07f 0239 05 0560 01 0014 02e8`). È l'unica differenza nel frame ed è la causa più
   probabile: va confermata con una prova sul tuo inverter.
2. **Protezione SoC.** In SolarGo la DoD in rete si modifica solo con la protezione SoC
   attiva: se è disattivata (offset 56 = 1) l'app nasconde il campo e reinvia la DoD invariata.
   Se in HA la protezione SoC è spenta, la DoD scritta potrebbe essere ignorata.

Nell'altra schermata (`AdvancedSettingNewActivity`) SolarGo ha anche un percorso che usa il
comando `0352` (tensione e corrente di scarica + DoD), ma per il tuo modello quella sezione è
nascosta (`ll_battery_content` = `gone`): il percorso effettivo è quello qui sopra.

## Cosa il portale NON scrive (mai)

Paese di sicurezza (`0301`, `023A`, `033C`), soglie e tempi di tensione e frequenza, parametri
di connessione, anti-islanding, curve P(f)/Q(U)/cosφ (registri `0x0001`, `0x0100`,
`0x0579`–`0x0594`, `0x7150`–`0x7154`, Modbus `0xB158`–`0xB2A0`…). In `app/transport.py` le
scritture sono limitate a una lista esplicita di comandi e registri ammessi: qualsiasi altro
frame di scrittura viene bloccato prima dell'invio, e i test lo verificano.

## Non ancora implementato (da decidere dopo i test)

- Interruttore della limitazione d'immissione (`0353`): la polarità ricavata dal codice
  decompilato è ambigua.
- Uscita backup (`0327`), scansione ombre (`0328`), fattore di potenza (`031F`), attivazione
  batteria (`0337`), controllo qualità rete (`035B`), parametri di carica: per ora solo lettura.
