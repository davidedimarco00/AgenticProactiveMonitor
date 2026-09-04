# AgenticProactiveMonitor

**Hybrid Multi-Agent System for Infrastructure Monitoring and Anomaly Detection**

AgenticProactiveMonitor è un sistema ibrido multi-agente per il monitoraggio proattivo dell’infrastruttura e il rilevamento di anomalie. Combina agenti autonomi, orchestrazione containerizzata e strumenti di osservabilità per raccogliere metriche, identificare anomalie e facilitare la risposta operativa.

---

## ✅ Panoramica rapida

- Monitoraggio continuo di risorse e servizi infrastrutturali.
- Rilevamento anomalie precoce (SINGLE_ENTITY detectors + correlazione multi-segnale).
- Coordinamento di agenti con ruoli distinti: raccolta, analisi, decisione e notifica / remediation.
- Obiettivo: ridurre MTTD e MTTR tramite automazioni e suggerimenti operativi.

---

## 🔭 Architettura (overview)

Di seguito due diagrammi che illustrano la topologia dei componenti e il flusso di telemetria.

Component overview (mermaid):

```mermaid
graph TD
  subgraph Monitored_System
    TG[Traffic Generator]
    AGW[API Gateway]
    APP[Processing & Data Services]
    WORK[Worker Service]
  end

  subgraph Infrastructure
    OL[Ollama]
    OS[OpenSearch]
    OD[OpenSearch Dashboards]
    QR[Qdrant]
    MDB[MongoDB]
    MCP[MCP Server]
    XMPP[Prosody XMPP]
    BE[Agentic Backend (SPADE + FastAPI)]
    DASH[Operator Dashboard]
  end

  TG --> AGW --> APP --> WORK
  APP -->|metrics & logs| OS
  WORK -->|metrics & logs| OS

  OS --> BE
  BE --> MDB
  BE --> QR
  BE --> MCP
  BE --> XMPP
  BE --> DASH
  OL --> BE
```

Flusso di telemetria e gestione incidenti:

```mermaid
flowchart LR
  Monitored[Monitored System] -->|metrics, logs| Telegraf[Telegraf / Fluent Bit] --> OpenSearch[OpenSearch]
  OpenSearch -->|anomaly event| AgenticBackend[Agentic Backend (autonomous agents)]
  AgenticBackend -->|incident write| MongoDB[(MongoDB)]
  AgenticBackend -->|RAG| Qdrant[(Qdrant)]
  AgenticBackend -->|notifications| Notifier[Notifier / Action Agent] -->|webhook/email/chat| Ops[Operator / Pager]
  AgenticBackend -->|observability| OperatorDashboard[Operator Dashboard (read-only)]
```

Nota: Ollama è in esecuzione nativa sul host Windows e viene usata come modello LLM locale.

---

## 🧩 Ruoli degli agenti

1. Collector Agent  
   Raccoglie metriche da host/servizi (CPU, RAM, disco), health endpoints e log.

2. Analyzer Agent  
   Applica regole, soglie e modelli per identificare deviazioni e segnali anomali.

3. Decision Agent  
   Correlazione eventi, scoring severità, definizione di azioni suggerite o automatiche.

4. Notifier / Action Agent  
   Invia alert (webhook/email/chat) e può innescare remediation (restart container, escalation, apertura ticket).

---

## ⚙️ Stack tecnologico (repo composition)
Basato sulla composizione del repository:
- Python: ~76.6% (backend, agent logic)
- CSS / JS / HTML: UI & dashboard
- PowerShell / Shell: script di orchestrazione e bootstrap
- Docker: packaging ed esecuzione containerizzata

---

## 📁 Struttura suggerita del repository

```text
.
├─ src/
│  ├─ agentic_backend/         # SPADE + FastAPI backend
│  ├─ agentic_dashboard/       # Flask operator dashboard (SPA)
│  ├─ infrastructure/          # docker-compose, bootstrap, scripts Ollama
│  ├─ monitored_system/        # servizi di esempio/traffic generator
│  └─ agents/                  # codice e configurazione degli agenti
├─ scripts/                    # script utili (start/stop/status/logs)
├─ .env.example
└─ README.md
```

Adatta i nomi e i percorsi alla struttura reale del repository se differiscono.

---

## 🧰 Prerequisiti

- Docker & Docker Compose
- Bash (Linux/macOS) o PowerShell/WSL su Windows
- Ollama (se usato come modello locale) installato e avviato sul host
- Variabili ambiente e segreti configurati (.env)

---

## 🚀 Quickstart (locale)

1) Clona il repository:
```bash
git clone https://github.com/davidedimarco00/AgenticProactiveMonitor.git
cd AgenticProactiveMonitor
```

2) Configura l'ambiente:
```bash
cp .env.example .env
# modifica .env: MongoDB password, XMPP credentials, endpoint Ollama se necessario
```

3) Avvia i servizi (infrastructure):
- Con Docker Compose (PowerShell / Bash):
```bash
cd src/infrastructure
docker compose up --build -d
```

4) Avvia il monitored-system:
```bash
cd src/monitored_system
docker compose up -d --build
```

5) Apri gli endpoint principali:
- OpenSearch: http://127.0.0.1:9200
- OpenSearch Dashboards: http://127.0.0.1:5601
- FastAPI Swagger: http://127.0.0.1:8082/docs
- Operator Dashboard: http://127.0.0.1:5050

Comandi utili:
```bash
# logs
docker compose logs -f agentic-backend
docker compose logs -f agentic-system-dashboard

# status
docker compose ps
```

---

## 🧪 Rilevamento anomalie: concetti

Detectors e logica possibili:
- Soglie statiche (CPU, memoria, error rate)
- Rilevamento di shift rispetto a baseline (rate/derivata)
- Correlazione multi-segnale (metriche + log + stato processi)
- Scoring e policy di escalation (severity → remediation / operator action)

---

## 🔔 Notifiche e remediation

Azioni configurabili:
- Webhook / Chat (XMPP / Slack / MS Teams)
- Email
- Restart di servizio / container
- Apertura automatica di ticket (integrazione con sistemi esterni)
- Escalation basata su severity e orario

---

## 🔐 Sicurezza e best practice

- Non committare segreti (.env, token). Usa secret managers.
- Limitare permessi per gli script di remediation.
- Registrare/audidare le azioni automatiche per post-mortem.
- Separare gli ambienti (dev/test/prod) e validare le remediation in ambienti controllati.

---

## 🛣️ Roadmap (proposta)

- [ ] Dashboard real-time con visualizzazioni incidenti
- [ ] Modello anomaly detection adattivo (online learning)
- [ ] Correlazione eventi cross-host e multi-entity detectors
- [ ] Plugin system per aggiungere nuovi agenti
- [ ] Test end-to-end e chaos testing
- [ ] Automazione test per pipeline CI

---

## 🤝 Contribuire

1. Fork del repository
2. Crea un branch feature: `git checkout -b feature/nome-feature`
3. Implementa e testa
4. Commit: `git commit -m "feat: descrizione"`
5. Push e apri una Pull Request

Per contribuzioni più grandi, apri prima un issue per discutere design/impatti.

---

## 📄 Licenza

Specifica la licenza del progetto (es. MIT, Apache-2.0). Se non presente, aggiungi un file `LICENSE`.

---

## 👤 Autore

**Davide Di Marco**  
GitHub: [@davidedimarco00](https://github.com/davidedimarco00)

---

## Note finali

- I diagrammi Mermaid sono resi su GitHub (supporto integrato); per una preview locale puoi usare estensioni VSCode per Mermaid o strumenti CLI.
- Se vuoi che aggiorni anche i READMEs secondari (src/infrastructure/README.md, src/agentic_dashboard/README.md) per uniformare gli schemi e i diagrammi, posso preparare le modifiche in batch.
