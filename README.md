# AgenticProactiveMonitor

**Hybrid Multi-Agent System for Infrastructure Monitoring and Anomaly Detection**

`AgenticProactiveMonitor` è un sistema ibrido multi-agente per il monitoraggio proattivo dell’infrastruttura e il rilevamento di anomalie.  
Il progetto combina automazione, orchestrazione in container e script di sistema per raccogliere metriche, identificare comportamenti anomali e facilitare la risposta operativa.

---

## 🚀 Obiettivi del progetto

- Monitorare in modo continuo risorse e servizi infrastrutturali.
- Rilevare anomalie in anticipo rispetto ai guasti critici.
- Coordinare agenti con ruoli distinti (raccolta, analisi, notifica, remediation).
- Ridurre il tempo di rilevazione (MTTD) e intervento (MTTR).

---

## 🧩 Architettura (overview)

Il sistema è organizzato come un insieme di agenti cooperanti:

1. **Collector Agent**  
   Raccoglie dati da host/servizi (CPU, RAM, disco, stato processi, health endpoint, log).

2. **Analyzer Agent**  
   Applica regole/soglie o logiche euristiche per identificare deviazioni e anomalie.

3. **Decision Agent**  
   Classifica severità, correla eventi e definisce azioni suggerite o automatiche.

4. **Notifier/Action Agent**  
   Invia alert (es. webhook/email/chat) e, se previsto, avvia azioni di remediation.

---

## ⚙️ Stack tecnologico

In base alla composizione linguistica del repository:

- **Shell (80.6%)** → script di orchestrazione/monitoraggio
- **Dockerfile (16.6%)** → packaging ed esecuzione containerizzata
- **JavaScript (2.8%)** → componenti di supporto/integrazione

---

## 📁 Struttura del repository (esempio)

> Adatta questa sezione alla struttura reale del progetto.

```text
.
├─ scripts/              # script shell principali
├─ agents/               # logica agenti (collector/analyzer/...)
├─ config/               # configurazioni e soglie
├─ docker/               # file e asset container
├─ logs/                 # output e log locali
└─ README.md
```

---

## 🔧 Prerequisiti

- **Docker** e **Docker Compose** (se usati)
- **Bash** (Linux/macOS o WSL su Windows)
- Variabili ambiente/configurazioni iniziali (vedi sezione Configurazione)

---

## 🛠️ Installazione

### 1) Clona il repository

```bash
git clone https://github.com/davidedimarco00/AgenticProactiveMonitor.git
cd AgenticProactiveMonitor
```

### 2) Configura l’ambiente

Crea un file `.env` (o usa il file di esempio se presente):

```bash
cp .env.example .env
```

Compila le variabili principali (endpoint, soglie, token notifiche, ecc.).

### 3) Avvia il sistema

#### Opzione A — con Docker Compose
```bash
docker compose up --build -d
```

#### Opzione B — con script shell
```bash
chmod +x scripts/*.sh
./scripts/start.sh
```

---

## ▶️ Utilizzo

### Avvio
```bash
./scripts/start.sh
```

### Stop
```bash
./scripts/stop.sh
```

### Status
```bash
./scripts/status.sh
```

### Log
```bash
./scripts/logs.sh
```

> Se i nomi script sono diversi, sostituiscili con quelli reali del repository.

---

## 🧪 Anomaly Detection (concept)

Il rilevamento anomalie può includere:

- superamento soglie statiche (CPU, memoria, latenza, error rate),
- variazioni improvvise rispetto a baseline,
- correlazione multi-segnale (metriche + log + stato servizi),
- scoring di severità per priorità intervento.

---

## 🔔 Notifiche e remediation

Esempi di azioni configurabili:

- notifica verso webhook/chat/email,
- restart di servizio/container,
- escalation in base alla severità,
- apertura ticket automatica (se integrata).

---

## 🔐 Sicurezza

- Non committare segreti in repository (`.env`, token, API key).
- Usa variabili ambiente e secret manager dove possibile.
- Limita permessi di esecuzione degli script.
- Valuta auditing/logging delle azioni automatiche di remediation.

---

## 📈 Roadmap (proposta)

- [ ] Dashboard real-time
- [ ] Modello anomaly detection adattivo
- [ ] Correlazione eventi cross-host
- [ ] Plugin system per nuovi agenti
- [ ] Test end-to-end e chaos testing

---

## 🤝 Contribuire

Contributi benvenuti!

1. Fai fork del repository
2. Crea un branch feature (`git checkout -b feature/nome-feature`)
3. Commit delle modifiche (`git commit -m "feat: aggiunge nuova feature"`)
4. Push del branch (`git push origin feature/nome-feature`)
5. Apri una Pull Request

---

## 📄 Licenza

Specifica qui la licenza del progetto (es. MIT, Apache-2.0, GPL-3.0).  
Se non hai ancora scelto una licenza, aggiungi un file `LICENSE`.

---

## 👤 Autore

**Davide Di Marco**  
GitHub: [@davidedimarco00](https://github.com/davidedimarco00)
