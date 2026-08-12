# Manifest-Driven Knowledge Ingestion

The `knowledge-base-ingest` one-shot container loads the Qdrant collections defined in `../collections.yaml` from the repository knowledge-base manifests.

The ingestion flow is:

```text
collections.yaml
      -> collection source_path
      -> manifest.yaml
      -> listed Markdown documents only
      -> YAML front matter + manifest metadata
      -> text chunks
      -> Ollama embeddings
      -> Qdrant payload + vector
```

The ingestion process is idempotent for repository-managed content. Before writing a successfully prepared collection, it deletes only points whose payload contains:

```text
managed_by = manifest-ingest
```

Manual documents that do not carry this marker are not removed.

Embeddings are fully prepared before the previous managed points are deleted. A failure while generating embeddings therefore does not erase the last valid repository-managed ingestion.

## Payload

Each chunk stores factual retrieval metadata such as:

- `kb_id`;
- `collection`;
- `document_type`;
- `roles`;
- `domains`;
- `services`;
- `topics`;
- `platform`;
- `source_files` or `source_urls` when available;
- `source_path`;
- `version`;
- `document_id`;
- chunk position and text;
- `managed_by=manifest-ingest`.

`incident_types` is deliberately not persisted by automatic ingestion because it must not become a diagnosis label used to leak evaluation answers.

## Windows / PowerShell test procedure

Run the commands from `src\infrastructure`.

First verify that the embedding model is installed on the Windows Ollama instance:

```powershell
ollama list
```

If required:

```powershell
ollama pull ibm/granite-embedding:30m
```

Validate the Compose file and build the modified services:

```powershell
docker compose config
docker compose build knowledge-base-ingest mcp-server
```

Run ingestion unit tests without starting external dependencies:

```powershell
docker compose run --rm --no-deps knowledge-base-ingest python -m unittest discover -s tests -v
```

Run MCP collection-routing unit tests:

```powershell
docker compose run --rm --no-deps mcp-server python -m unittest tests.test_qdrant_collection_routing -v
```

Start/rebuild the infrastructure. `qdrant-init` creates the six collections and `knowledge-base-ingest` runs automatically before the MCP server and Knowledge Base Web UI become available:

```powershell
docker compose up -d --build
```

Inspect ingestion output:

```powershell
docker compose logs knowledge-base-ingest
```

Then run the end-to-end Qdrant/Ollama smoke test:

```powershell
.\knowledge_base\tests\test_knowledge_base.ps1
```

At the current stage, `monitored-system` and `kb-system-engineer-linux` must contain vectors. The Network Engineer, Application Engineer, Software Developer and Technical Lead collections exist but can remain empty until their professional knowledge bases are populated.
