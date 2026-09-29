# OpenJM Enterprise AI

Clean rebuild of the OpenJM enterprise AI platform.

The previous Hermes-generated repository is treated as reference material only. This repository establishes the product and architecture from scratch around a proven vertical slice rather than a collection of disconnected screens.

## Vertical Slice 1 — complete

Vertical Slice 1 is merged to `main` and proves this path end to end:

```text
User
  ↓
OpenJM Chat
  ↓
Server-side Conversation Service
  ↓
OpenJM Orchestrator
  ├── GENERAL
  └── KNOWLEDGE
        ↓
     DB-GPT RAG
        ↓
     Evidence + citations
        ↓
OpenAI-compatible Model Gateway
  ↓
Persisted answer
```

The model gateway can point at Hermes, Ollama, vLLM or another OpenAI-compatible runtime.

DB-GPT is used behind an OpenJM-owned knowledge interface. It is not exposed as the product UI.

## Repository workflow

`main` is the baseline. Vertical Slice 1 is merged.

Current implementation work is on:

```text
build/vertical-slice-2-structured-data
```

Vertical Slice 2 adds governed read-only relational data access. See `docs/VERTICAL_SLICE_2.md` and `docs/ACCEPTANCE.md` before implementation or merge.

## Windows quick start

For the user's current Windows setup, the preferred path is now scripted.

From the repository root on the current build branch:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-windows.ps1
```

The setup script:

- confirms the correct Git branch;
- uses Python 3.11;
- creates/preserves the local `.env`;
- asks for the actual Hermes/OpenAI-compatible endpoint, model name and optional bearer key;
- installs backend dependencies;
- runs backend unit tests;
- installs frontend dependencies;
- performs a production frontend build.

Then start both services:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-windows.ps1
```

Run the memory acceptance check:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify-windows.ps1
```

Run memory + real document ingestion/catalog acceptance:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify-windows.ps1 -Document "C:\full\path\to\test-document.pdf"
```

If Hermes will perform the local setup, hand it `HERMES_LOCAL_SETUP_PROMPT.md`. That prompt explicitly prohibits direct pushes to `main`, fake success, scope expansion, and merging before the acceptance gates pass.

## Local development

### 1. Clone and switch to the build branch

```bash
git clone https://github.com/sjevans1/OpenJM-Enterprise-AI.git
cd OpenJM-Enterprise-AI
git switch build/vertical-slice-2-structured-data
```

### 2. Configure the backend

Copy the example environment file:

```bash
cp .env.example .env
```

The validated Vertical Slice 1 development target on the current workstation is the local Gemma 4 12B worker:

```text
http://127.0.0.1:18080/v1
```

with model identifier:

```text
gemma-4-12b-local
```

Use:

```env
OPENJM_MODEL_BASE_URL=http://127.0.0.1:18080/v1
OPENJM_MODEL_API_KEY=
OPENJM_MODEL_NAME=gemma-4-12b-local
OPENJM_MODEL_TIMEOUT_SECONDS=300
```

If the local worker requires a bearer key, set `OPENJM_MODEL_API_KEY` only in the local `.env`; never commit it. These are validated development defaults for this workstation, not a product limitation—the model gateway remains interchangeable with other OpenAI-compatible local runtimes.

### 3. Start the backend

Python 3.11 is currently required because DB-GPT 0.8.2 pins aiohttp 3.8.4, which does not build on Python 3.12.

With `uv`:

```bash
cd backend
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
```

On Windows PowerShell use the appropriate virtual-environment activation command instead of `source`.

The first DB-GPT embedding request will download the configured HuggingFace embedding model unless it is already cached.

### 4. Start the frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open:

```text
http://127.0.0.1:5173
```

## Runtime acceptance

With the backend running:

```bash
python scripts/acceptance.py
```

That test creates one conversation, tells OpenJM the name `Sam`, asks for the name again, and verifies that the conversation is persisted on the server.

To also test real DB-GPT ingestion and the document catalog:

```bash
python scripts/acceptance.py --document /path/to/test-document.pdf
```

To automate the full document-grounded Gate C plus deletion negative test:

```bash
python scripts/acceptance.py \
  --document /path/to/test-document.txt \
  --question "What is the unique fact in this document?" \
  --expect "expected-answer-fragment" \
  --delete-after-test
```

## Product UI

OpenJM exposes only implemented product workflows:

- **Chat** — persistent conversations, routing, answers and evidence.
- **Knowledge** — governed document upload/index state.

Vertical Slice 2 will enable **Data** as the next real product workflow. **Reports, Automations and Administration** remain future capabilities until their implementation phases.

There is no primary Jobs screen and no raw MMR/relevance debugging workflow in the user product.

## Architecture and acceptance criteria

See:

- `docs/ARCHITECTURE.md`
- `docs/ACCEPTANCE.md`
- `docs/VERTICAL_SLICE_2.md`

## Build rules

- No simulated production success.
- No browser-direct model calls.
- No browser-direct DB-GPT calls.
- Conversation state belongs to the server.
- Evidence is retrieved before generation.
- Document inventory is a deterministic catalog operation, not vector similarity search.
- OpenJM owns policy, audit, routing and UX.
- Third-party open-source components remain replaceable behind OpenJM interfaces.
- A capability is not complete until its automated and runtime acceptance checks pass.
