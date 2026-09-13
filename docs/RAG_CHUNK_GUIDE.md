# RAG Chunk Guide — VLTHR DEVOPS

> Created: Jul 29, 2026
> Status: COMPLETE
> Output: `rag_chunks/` (9 MB, 4,744 chunks, 2.25M tokens)

---

## 1. What This Is

The `rag_chunks/` directory contains every text file in the VLTHR DEVOPS repository, broken into searchable chunks. Each chunk is a segment of source code, documentation, configuration, or data, sized at roughly 500 tokens with 50-token overlap between adjacent chunks. The chunks are stored as JSONL files, one per folder grouping, with a master index and stats file.

This is the retrieval layer for RAG (Retrieval-Augmented Generation). An AI agent or search tool reads the chunk files, finds segments matching a query, and uses the retrieved content as context to answer questions about the codebase.

---

## 2. Directory Structure

```
rag_chunks/
  ├── _index.json              Master index: folder -> files -> chunk counts
  ├── _stats.json              Global statistics
  ├── root.jsonl               Root-level files (.env, docker-compose.yml, README.md, etc.)
  ├── pipeline.jsonl           pipeline/ top-level (Dockerfile, requirements.txt)
  ├── pipeline_engine.jsonl    pipeline/engine/ (core trading logic, 398 chunks)
  ├── pipeline_engine_patterns.jsonl   Pattern detection modules
  ├── pipeline_engine_v3.jsonl         V3 engine modules (discovery, ingestion, core)
  ├── backend.jsonl            backend/ top-level
  ├── backend_src.jsonl        backend/src/ (server.cjs, db.cjs, etc.)
  ├── backend_src_routes.jsonl backend/src/routes/ (API route handlers)
  ├── frontend.jsonl           frontend/ top-level
  ├── frontend_src.jsonl       frontend/src/ (App.tsx, config, types)
  ├── frontend_src_pages.jsonl frontend/src/pages/ (8 page components)
  ├── frontend_src_components.jsonl    frontend/src/components/ (20 shared components)
  ├── telegram.jsonl           telegram/ (3-bot polling service, 12 files)
  ├── docs.jsonl               docs/ root (AGENT_HANDOFF, ARCHITECTURE, etc.)
  ├── docs_memory.jsonl        docs/memory/ (session reports, handoffs, 690 chunks)
  ├── docs_plans.jsonl         docs/plans/ (frontend refactor, trading centre upgrade)
  ├── docs_reference.jsonl     docs/reference/ (Dockerfiles, strategies.csv, etc.)
  ├── docs_workflows.jsonl     docs/workflows/ (CCP, PIVP, RODP, GBEGP, CITM)
  ├── docs_skill.jsonl         docs/skill/ (core philosophy, architecture rules)
  ├── workflows.jsonl          workflows/ (33 VLTHR workflow .md files)
  ├── workflows_v1.jsonl       workflows/v1/ (32 v1 workflow .md files)
  ├── .windsurf_workflows.jsonl  .windsurf/workflows/ (11 active workflow files)
  ├── backtest_phase1_archive.jsonl  Phase 1 archived code and results
  ├── business_data.jsonl      business_data/ (CRM, marketing CSVs, migration SQL)
  ├── data-ingestion_workers.jsonl   Ingestion scheduler and core scripts
  ├── data_bybit.jsonl         data/bybit/ (ingestion summaries, market data summaries)
  ├── data_bybit_workers.jsonl data/bybit/workers/ (mirror ingestion scripts)
  ├── data_bybit_ws.jsonl      data/bybit/ws/ (WebSocket ticker/orderbook snapshots)
  ├── postgres.jsonl           postgres/db_migration.py (schema creation)
  ├── redis.jsonl              redis/redis.conf
  ├── exports.jsonl            exports/ (regime analysis)
  ├── ngrok.jsonl              ngrok/ngrok.yml
  ├── scripts.jsonl            scripts/gen-icons.cjs
  └── ... (66 folder files total)
```

---

## 3. Chunk Format

Each line in a JSONL file is one chunk. The JSON schema:

```json
{
  "id": "chunk_373b6e10_0000",
  "file_path": "pipeline/engine/portfolio_orchestrator.py",
  "folder": "pipeline_engine",
  "file_type": "py",
  "file_size": 117112,
  "line_start": 1,
  "line_end": 43,
  "token_count": 494,
  "content": "import os\nimport sys\nimport json\n..."
}
```

### Fields

- **id**: Unique identifier. Format: `chunk_<8-char-hash>_<4-digit-index>`. Hash is MD5 of the file path. Index is the chunk position within the file (0-based).
- **file_path**: Relative path from the DEVOPS root to the source file.
- **folder**: Folder grouping key. Derived from the first 2-3 path components. Used as the JSONL filename.
- **file_type**: File extension without the dot (py, md, tsx, cjs, json, yml, html, etc.).
- **file_size**: Source file size in bytes.
- **line_start**: First line number in this chunk (1-indexed).
- **line_end**: Last line number in this chunk (1-indexed, exclusive of next chunk start).
- **token_count**: Number of tokens in the content, counted by tiktoken's `cl100k_base` encoding (same as GPT-4/Claude tokenization).
- **content**: The actual text content of the chunk. Raw source code, markdown, JSON, or config text.

---

## 4. Chunking Parameters

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Chunk size | 500 tokens | Large enough to capture a complete function or section. Small enough to be precise in retrieval. |
| Overlap | 50 tokens | Prevents losing context at chunk boundaries. A function split across two chunks will have overlapping content. |
| Max file size | 2 MB | Files larger than 2 MB are skipped. These are typically binary or raw data files not useful for text retrieval. |
| Max line length | 2000 chars | Lines longer than 2000 characters are truncated. Prevents minified files or base64 blobs from creating oversized chunks. |
| Tokenizer | tiktoken `cl100k_base` | Matches GPT-4 and Claude tokenization. Provides accurate token counts for context window planning. |

---

## 5. What Was Chunked

### File Types Processed

Python (.py), JavaScript (.js, .cjs, .mjs), TypeScript (.ts, .tsx, .jsx), Markdown (.md), JSON (.json), YAML (.yml, .yaml), HTML (.html), CSS (.css), Shell (.sh, .bash), SQL (.sql), CSV (.csv), Environment (.env), Config (.conf, .cfg, .ini), Dockerfiles, Gitignore files, SVG files, and plain text (.txt, .log, .lock).

### Directories Skipped

`__pycache__`, `.pytest_cache`, `node_modules`, `dist`, `.git`, `rag_chunks` (self), `logs` (runtime log output).

### Files Skipped

`package-lock.json` (150 KB of lock data, not useful for retrieval). Files larger than 2 MB (binary data, large CSVs, parquet files).

### Removed After Initial Run

Six JSONL files containing raw CSV/log data were removed to reduce bloat from 1.1 GB to 9 MB:
- Backup parquet metadata (494 MB)
- Binance metrics CSVs (483 MB)
- DB export CSVs (56 MB)
- Replay log files (20 MB)
- Backtest result CSVs (4.3 MB)
- Data folder CSVs (3.8 MB)

These were raw numerical data files. They can be re-chunked later if needed by adjusting the skip list in `rag_chunker.py`.

---

## 6. Stats Summary

| Metric | Value |
|--------|-------|
| Total files chunked | 915 |
| Total chunks | 4,744 |
| Total tokens | 2,250,278 |
| Output size | 8.98 MB |
| Folders indexed | 66 |
| Avg chunks per file | 5.2 |
| Avg tokens per chunk | 474 |

### Top 10 Folders by Chunk Count

| Folder | Chunks | Files | Description |
|--------|--------|-------|-------------|
| docs_memory | 690 | 10 | Session reports, handoff documents, knowledge packets |
| docs | 634 | 21 | Architecture, project overview, setup, logging, performance reviews |
| pipeline_engine | 398 | 47 | Core trading engine (orchestrator, scorer, executor, gates, config) |
| departments_strategy_research_BACKTESTER | 198 | 18 | Shared strategy modules |
| business_data | 148 | 24 | Business operations data (CRM, marketing, team) |
| backtest_phase1_archive | 140 | 8 | Phase 1 archived backtest code and results |
| frontend_public | 143 | 6 | Static assets, PWA manifest, service worker |
| frontend_src_components | 110 | 20 | React shared components (AuthPage, SignalCard, etc.) |
| pipeline_engine_v3 | 108 | 21 | V3 engine (discovery, ingestion, core, orchestrator) |
| frontend_src_pages | 106 | 8 | React pages (Dashboard, Paper, Demo, VTC, etc.) |

---

## 7. How to Retrieve Data

### 7.1 Manual Retrieval (grep / jq)

The simplest way to search chunks is with standard Unix tools:

**Search all chunks for a keyword:**
```bash
cd rag_chunks
grep -l "bybit_executor" *.jsonl
```

**Search within a specific folder's chunks:**
```bash
grep "set_trading_stop" pipeline_engine.jsonl | head -5
```

**Extract readable content from matching chunks:**
```bash
grep "portfolio_orchestrator" pipeline_engine.jsonl | python3 -c "
import sys, json
for line in sys.stdin:
    c = json.loads(line)
    print(f'--- {c[\"file_path\"]} lines {c[\"line_start\"]}-{c[\"line_end\"]} ---')
    print(c['content'][:500])
    print()
"
```

**Find all chunks from a specific file:**
```bash
python3 -c "
import json
with open('pipeline_engine.jsonl') as f:
    for line in f:
        c = json.loads(line)
        if c['file_path'] == 'pipeline/engine/portfolio_orchestrator.py':
            print(f'Chunk {c[\"id\"]}: lines {c[\"line_start\"]}-{c[\"line_end\"]}, {c[\"token_count\"]} tokens')
"
```

### 7.2 Python Retrieval Script

For programmatic access, use this pattern:

```python
import json
from pathlib import Path

def search_chunks(query: str, folder: str = None, file_type: str = None) -> list[dict]:
    """Search chunks by keyword. Returns matching chunk objects."""
    rag_dir = Path("rag_chunks")
    results = []

    for jsonl in rag_dir.glob("*.jsonl"):
        if folder and jsonl.stem != folder:
            continue
        with open(jsonl) as f:
            for line in f:
                chunk = json.loads(line)
                if file_type and chunk["file_type"] != file_type:
                    continue
                if query.lower() in chunk["content"].lower():
                    results.append(chunk)

    return results

def get_file_chunks(file_path: str) -> list[dict]:
    """Get all chunks for a specific file, in order."""
    rag_dir = Path("rag_chunks")
    results = []

    for jsonl in rag_dir.glob("*.jsonl"):
        with open(jsonl) as f:
            for line in f:
                chunk = json.loads(line)
                if chunk["file_path"] == file_path:
                    results.append(chunk)

    return sorted(results, key=lambda c: c["line_start"])

def get_folder_chunks(folder_key: str) -> list[dict]:
    """Get all chunks from a specific folder grouping."""
    rag_dir = Path("rag_chunks")
    jsonl = rag_dir / f"{folder_key}.jsonl"
    if not jsonl.exists():
        return []

    results = []
    with open(jsonl) as f:
        for line in f:
            results.append(json.loads(line))
    return results

def list_folders() -> dict:
    """List all indexed folders with their stats."""
    with open("rag_chunks/_index.json") as f:
        return json.load(f)
```

### 7.3 Using the Master Index

The `_index.json` file maps each folder key to its chunk file, file list, and counts:

```python
import json

with open("rag_chunks/_index.json") as f:
    index = json.load(f)

# List all folders
for folder, info in sorted(index.items()):
    print(f"{folder}: {info['chunk_count']} chunks, {info['file_count']} files")

# Find which folder contains a specific file
for folder, info in index.items():
    if "portfolio_orchestrator.py" in info["files"]:
        print(f"Found in: {folder} -> {info['chunk_file']}")
```

### 7.4 Loading All Chunks Into Memory

For full-text search across all 4,744 chunks (uses ~50 MB RAM):

```python
import json
from pathlib import Path

all_chunks = []
for jsonl in Path("rag_chunks").glob("*.jsonl"):
    with open(jsonl) as f:
        for line in f:
            all_chunks.append(json.loads(line))

print(f"Loaded {len(all_chunks)} chunks")

# Simple keyword search
matches = [c for c in all_chunks if "bybit" in c["content"].lower()]
print(f"Found {len(matches)} chunks mentioning 'bybit'")
```

---

## 8. What to Use This For

### 8.1 Codebase Q&A

Ask questions about the codebase and retrieve relevant chunks as context:
- "How does the Bybit executor authenticate?" -> Search for "HMAC" or "bybit_executor" in `pipeline_engine.jsonl`
- "What are the 12 pipeline gates?" -> Search for "gate" in `pipeline_engine.jsonl` and `workflows.jsonl`
- "How does the frontend display paper trades?" -> Search for "paper" in `frontend_src_pages.jsonl`

### 8.2 Debugging Context

When debugging an issue, pull all chunks related to the problem area:
- Bybit sizing bug -> `grep "bybit_dollar_risk" pipeline_engine.jsonl`
- SL sync issue -> `grep "set_trading_stop" pipeline_engine.jsonl`
- Signal veto problem -> `grep "DQS" pipeline_engine.jsonl` and `grep "veto" pipeline_engine.jsonl`

### 8.3 Architecture Understanding

New agents can read the index to understand the project structure, then pull specific chunks for deep dives:
- Start with `docs.jsonl` and `docs_memory.jsonl` for project history and decisions
- Move to `pipeline_engine.jsonl` for core trading logic
- Check `workflows.jsonl` and `.windsurf_workflows.jsonl` for operational protocols

### 8.4 Cross-Reference Discovery

Find all references to a concept across the entire codebase:
- "Kelly" appears in pipeline engine, backtest, workflows, and docs
- "calibration" appears in pipeline engine, backtest, docs, and config files
- "TIME_EXIT" appears in orchestrator, backend, telegram, and docs

### 8.5 Embedding Readiness

The chunks are pre-sized and tokenized. To add vector embeddings later:
1. Load each chunk's `content` field
2. Pass through an embedding model (local: all-MiniLM-L6-v2, API: NVIDIA/OpenAI)
3. Store the embedding vector alongside the chunk metadata
4. Build a vector index (FAISS, ChromaDB, or SQLite with vector extension)

The chunk IDs are stable (hash-based), so embeddings can be cached and incrementally updated.

---

## 9. Re-Running the Chunker

The chunker script is at `rag_chunker.py` in the DEVOPS root. To re-run:

```bash
cd /path/to/DEVOPS
python3 rag_chunker.py
```

### Adjustable Parameters (in the script)

| Variable | Default | Description |
|----------|---------|-------------|
| `CHUNK_SIZE` | 500 | Target tokens per chunk |
| `CHUNK_OVERLAP` | 50 | Token overlap between adjacent chunks |
| `MAX_FILE_SIZE` | 2,000,000 | Skip files larger than this (bytes) |
| `MAX_LINE_LENGTH` | 2000 | Truncate lines longer than this (chars) |
| `TEXT_EXTENSIONS` | see script | File extensions to process |
| `SKIP_DIRS` | see script | Directories to skip entirely |
| `SKIP_PATTERNS` | see script | File name patterns to skip |

### Adding Embeddings Later

To add embeddings without re-chunking, write a separate script that reads the JSONL files and generates embeddings for each chunk's `content` field. The chunk IDs are stable, so you can join embeddings back to chunks by ID.

---

## 10. Limitations and Notes

- **No semantic search**: Current setup is text-only. Retrieval is keyword-based (grep, string matching). Semantic search requires adding embeddings.
- **No binary files**: Parquet files, images (.jpg, .png), pickle files (.pkl), and compiled binaries are not chunked. Their metadata appears in the index but content is absent.
- **CSV files are chunked**: Business data CSVs are included. Large data CSVs (backtest results, exports, Binance metrics) were removed manually. Adjust `SKIP_PATTERNS` in the script to control this.
- **Backup duplication**: The `backups/20260710_142836/` folder contains copies of files that also exist in the main tree. This means some content appears twice in the chunks. Filter by `file_path` to deduplicate.
- **Token counts are approximate**: tiktoken's `cl100k_base` is used. Actual token consumption may vary slightly depending on the model used for generation.
- **Overlap is line-based**: The 50-token overlap is implemented by carrying the last few lines of the previous chunk into the next. This means overlap is approximate, not exact token count.

---

## 11. File Inventory

### Core Files

| File | Location | Description |
|------|----------|-------------|
| `rag_chunker.py` | DEVOPS root | The chunking script. Re-run to regenerate chunks. |
| `_index.json` | `rag_chunks/` | Master index. 66 folder entries with file lists and chunk counts. |
| `_stats.json` | `rag_chunks/` | Global statistics. Total files, chunks, tokens, size. |

### JSONL Files by Category

**Pipeline (core trading):**
- `pipeline_engine.jsonl` (398 chunks) — orchestrator, scorer, executor, gates, config, safety layer, signal state, regime HMM, v2 filters
- `pipeline_engine_patterns.jsonl` (36 chunks) — pivot detector, trend lines, support/resistance, pattern tests
- `pipeline_engine_v3.jsonl` (108 chunks) — v3 orchestrator, discovery (hypothesis, probe), ingestion (bybit client, parquet store), core (evidence store, source registry)
- `pipeline_engine_tests.jsonl` (1 chunk) — safety layer test
- `pipeline_engine_v2.jsonl` (1 chunk) — v2 init

**Backend (API server):**
- `backend_src.jsonl` (132 chunks) — server.cjs (main), db.cjs, bybit-client.cjs, parquet-reader.cjs, vtc-engine.cjs, utility scripts
- `backend_src_routes.jsonl` (23 chunks) — auth, paper, power, refresh, signals routes
- `backend_src_auth.jsonl` (1 chunk) — TOTP implementation
- `backend.jsonl` (4 chunks) — Dockerfile, package.json, .env, .dockerignore

**Frontend (React SPA):**
- `frontend_src_components.jsonl` (110 chunks) — AuthPage, SignalCard, Charts, modals, loading states, error boundaries
- `frontend_src_pages.jsonl` (106 chunks) — Dashboard, Paper, Demo, VTC, Signals, Power, Backtest, Watchlist pages
- `frontend_src.jsonl` (64 chunks) — App.tsx, config.ts, types.ts, index.css, main.tsx
- `frontend_src_stores.jsonl` (5 chunks) — backtestStore.ts
- `frontend_src_hooks.jsonl` (6 chunks) — useAuth.ts, useSSE.ts
- `frontend_public.jsonl` (143 chunks) — PWA manifest, service worker, mobile app HTML, favicon, icons
- `frontend.jsonl` (11 chunks) — Dockerfile, nginx.conf, vite.config, tsconfig, eslint config
- `frontend_docs.jsonl` (10 chunks) — UI upgrade doc
- `frontend_src_api.jsonl` (1 chunk) — auth.ts
- `frontend_src_assets.jsonl` (2 chunks) — react.svg, vite.svg

**Data Ingestion:**
- `data-ingestion_workers.jsonl` (88 chunks) — scheduler, core ingestion, enrichment, market data, telegram alerts, proxy tests
- `data-ingestion_workers_<SYMBOL>.jsonl` (3 chunks each, 6 symbols) — per-symbol fetch scripts
- `data-ingestion.jsonl` (2 chunks) — Dockerfile, requirements.txt

**Documentation:**
- `docs.jsonl` (634 chunks) — AGENT_HANDOFF, ARCHITECTURE, PROJECT_OVERVIEW, SETUP, LOGGING, BTC_STRATEGY_RESEARCH_BRIEF, SESSION_PLAN, BYBIT_PERFORMANCE_REVIEW, HTML reports
- `docs_memory.jsonl` (690 chunks) — session handoffs, improvement plans, competitive research, pipeline audit, knowledge packets
- `docs_plans.jsonl` (124 chunks) — frontend refactor plan, trading centre upgrade plan, pattern feature spec
- `docs_reference.jsonl` (94 chunks) — reference Dockerfiles, compose variants, strategies.csv, deep research report, watchlist plan
- `docs_workflows.jsonl` (21 chunks) — CCP, CITM, GBEGP, PIVP, RODP workflow definitions
- `docs_skill.jsonl` (8 chunks) — core philosophy, architecture rules, coding standards, safety invariants
- `docs_architecture.jsonl` (40 chunks) — session regime parameterization plan
- `docs_handoff.jsonl` (30 chunks) — system overview handoff

**Workflows:**
- `workflows.jsonl` (122 chunks) — 33 VLTHR workflow definitions (abstain-gate, adcos, architecture, backtest-strategy, calibration, crds, decision-engine, etc.)
- `workflows_v1.jsonl` (89 chunks) — 32 v1 workflow definitions
- `.windsurf_workflows.jsonl` (51 chunks) — 11 active Windsurf workflows (CITM, CCP, GBEGP, PIVP, RODP, etc.)

**Telegram:**
- `telegram.jsonl` (63 chunks) — orchestrator, telegram_service, polling_service, database_operations, file_operations, pipeline_operations, server_operations, test files

**Infrastructure:**
- `postgres.jsonl` (17 chunks) — db_migration.py (schema creation, 28KB)
- `redis.jsonl` (1 chunk) — redis.conf
- `ngrok.jsonl` (1 chunk) — ngrok.yml
- `root.jsonl` (117 chunks) — .env, docker-compose.yml, README.md, vlthr-scale-audit.html

**Backtest:**
- `backtest_phase1_archive.jsonl` (140 chunks) — Phase 1 archived code (runner, orchestrator, config, gates, scorer, calibration)

**Business:**
- `business_data.jsonl` (148 chunks) — 24 CSV/SQL/Python files for business operations

**Backups:**
- `backups.jsonl` (80 chunks) — 3 HTML backup files
- `backups_20260710_142836.jsonl` (12 chunks) — .env and docker-compose from Jul 10 backup
- `backups_20260710_142836_business_data.jsonl` (148 chunks) — business data from backup
- `backups_20260710_142836_postgres.jsonl` (15 chunks) — db_migration.py from backup
- `backups_20260710_142836_redis.jsonl` (1 chunk) — redis.conf from backup

**Data:**
- `data_bybit.jsonl` (13 chunks) — ingestion summaries, market data summaries
- `data_bybit_<SYMBOL>.jsonl` (64-75 chunks each, 6 symbols) — per-symbol parquet metadata
- `data_bybit_workers.jsonl` (106 chunks) — mirror ingestion scripts
- `data_bybit_ws.jsonl` (10 chunks) — WebSocket ticker and orderbook JSON snapshots
- `data_bybit_docs.jsonl` (6 chunks) — Bybit data ingestion skill doc

**Other:**
- `exports.jsonl` (34 chunks) — regime analysis markdown
- `scripts.jsonl` (1 chunk) — gen-icons.cjs
- `ai-trader-engine.jsonl` (25 chunks) — legacy FastAPI engine
- `ai-trader-engine_pipeline.jsonl` (29 chunks) — legacy pipeline modules
- `departments_strategy_research_BACKTESTER.jsonl` (198 chunks) — shared strategy modules
