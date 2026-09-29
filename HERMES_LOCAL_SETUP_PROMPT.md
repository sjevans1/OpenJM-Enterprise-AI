# Hermes Local Setup Prompt — OpenJM Enterprise AI

Use this prompt only for configuring and running the clean OpenJM rebuild on the user's Windows PC. Do not redesign the architecture, do not copy code from the legacy `enterprise-ai-platform` repository, and do not push directly to `main`.

## Repository

https://github.com/sjevans1/OpenJM-Enterprise-AI

Required branch:

`build/vertical-slice-1`

A draft PR already exists. Work only on this branch unless explicitly instructed otherwise.

## Your role

Act as the local implementation/operator for the user's machine. Your immediate objective is to get Vertical Slice 1 running locally and prove the acceptance gates. Do not add SQL, Hybrid, Reports, Automations, agents, or other phases.

## Required sequence

1. Locate or clone the repository.
2. Fetch latest changes.
3. Switch to `build/vertical-slice-1`.
4. Confirm you are NOT on `main`.
5. Inspect `.env.example`, `README.md`, `docs/ARCHITECTURE.md`, and `docs/ACCEPTANCE.md`.
6. Determine the actual local Hermes/OpenAI-compatible API endpoint, current model identifier, and whether it requires a bearer/API key. Do not guess these values.
7. Configure the repo's local `.env` with those actual values. Never commit `.env` or secrets.
8. Run the Windows setup script:

   `powershell -ExecutionPolicy Bypass -File .\scripts\setup-windows.ps1`

   If you already know the endpoint/model, pass them explicitly:

   `powershell -ExecutionPolicy Bypass -File .\scripts\setup-windows.ps1 -ModelBaseUrl "<actual-base-url>" -ModelName "<actual-model-name>"`

9. Resolve any dependency/install errors on the build branch only. Prefer minimal fixes. Do not replace DB-GPT with a hand-built fake retrieval implementation.
10. Start the application:

    `powershell -ExecutionPolicy Bypass -File .\scripts\start-windows.ps1`

11. Verify:
    - GET `http://127.0.0.1:8000/api/health` returns healthy.
    - frontend loads at `http://127.0.0.1:5173`.
    - UI renders as the new OpenJM shell, not the legacy Chat/Documents/Jobs interface.

12. Run baseline acceptance:

    `powershell -ExecutionPolicy Bypass -File .\scripts\verify-windows.ps1`

13. Manually verify in the UI:
    - send `Hello, my name is Sam.`
    - send `What is my name?`
    - OpenJM must answer Sam from persisted server-side history.
    - refresh the browser.
    - reopen the same conversation from Recent conversations.
    - ask `What is my name?` again.
    - OpenJM must still answer Sam.

14. Pick a real test document containing an obvious unique fact and run:

    `powershell -ExecutionPolicy Bypass -File .\scripts\verify-windows.ps1 -Document "<full-path-to-document>"`

15. Then verify in Chat:
    - `What documents do you have loaded?`
    - the uploaded document must be returned from the document catalog.
    - ask a natural-language question whose answer exists only in that document.
    - the answer must route as KNOWLEDGE and show evidence from the document.

## Hard constraints

- Do not push directly to `main`.
- Do not merge the draft PR.
- Do not create a Jobs tab.
- Do not expose raw vector/MMR scores as the primary user experience.
- Do not claim ingestion succeeded unless DB-GPT actually indexed the document.
- Do not claim RAG works merely because a search endpoint returns chunks.
- Do not simulate a successful model response, document index, report, or job.
- Do not replace persistent server-side conversation history with browser-only memory.
- Do not let the browser call Hermes, DB-GPT, or databases directly.
- Do not add later-phase functionality until all Vertical Slice 1 acceptance gates pass.
- If Hermes is running in WSL and OpenJM is running on Windows, explicitly verify networking between them rather than assuming localhost resolves across the boundary.
- Preserve the OpenJM-owned interfaces around model and knowledge providers.

## When something fails

Work from evidence:
- capture the exact command;
- capture the exact error;
- identify whether failure is Python/dependency, DB-GPT, model endpoint, network, backend, or frontend;
- make the smallest justified fix on `build/vertical-slice-1`;
- rerun the failed check;
- rerun the relevant acceptance test.

Do not respond with "implemented" until the test actually passes.

## Final report required

At completion, report:

1. Current Git branch.
2. Exact model endpoint and model identifier used (never reveal the secret key).
3. Backend health result.
4. Frontend build result.
5. Unit-test result.
6. Sam memory test result before browser refresh.
7. Sam memory test result after browser refresh/reopen.
8. Document ingestion result and filename.
9. Document catalog result.
10. Document-grounded Q&A result with whether evidence was returned.
11. Every code change made locally and its commit SHA.
12. Anything still failing.

Do not merge the PR. Stop after Vertical Slice 1 is demonstrably working.
