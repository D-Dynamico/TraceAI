# Architecture

Three views of the same system: what the pieces are, how a document gets *in*,
and how a question gets *answered*. Drawn from the code rather than from the
sketch in [plan.md §3](../plan.md) — where the two disagree, these are right.

For *why* each of these is shaped the way it is, see
[engineering-notes.md](engineering-notes.md).

---

## System

```mermaid
flowchart TB
    subgraph browser["Browser · React + Vite · deployed on Vercel"]
        views["Four views, one nav, no router<br/>Timeline · Graph · Search · Upload"]
        client["api/client.js<br/>every call carries X-User-Id"]
        views --> client
    end

    subgraph api["FastAPI · deployed on Render"]
        ident["identity.current_user<br/>X-User-Id → user_id, strict allowlist"]
        routes["Routes<br/>/upload · /ingest-url · /ingest-text · /documents<br/>/search · /answer · /graph · /career-paths · /seed-demo · /health"]

        subgraph ingest["Ingestion"]
            parser["file_parser<br/>PDF · DOCX · PPTX · TXT · images"]
            ocr["ocr_handler<br/>local Tesseract first, Gemini Vision second"]
            scraper["url_scraper → github_scraper · web_scraper"]
            guard["url_guard · the SSRF gate<br/>scheme · public IPs only · every redirect hop · 5 MB cap"]
            typed["text_entry<br/>an achievement with no document at all"]
            parser --> ocr
            scraper --> guard
        end

        subgraph aimod["AI layer"]
            cat["categorizer"]
            vis["vision"]
            career["career_path"]
            ragm["rag"]
            router["query_router<br/>deterministic filter-vs-semantic routing<br/>no Gemini call, so search costs no quota"]
            rel["relationship_engine<br/>Layer A shared-skill hubs · Layer B cosine edges"]
            limiter["ai/gemini.py<br/>ONE shared 13s rate limiter · key redaction<br/>every caller degrades, none raises"]
            embed["embeddings · MiniLM-L6-v2<br/>ONNX, local, free, not rate-limited<br/>~900-char windows, 150 overlap, 40-chunk cap"]
            pre["precomputed<br/>shipped vectors for the demo profile's texts<br/>keyed by SHA-256 of the exact string"]
            cat --> limiter
            vis --> limiter
            career --> limiter
            ragm --> limiter
            embed -- "hit · skips the model entirely" --> pre
        end

        builder["graph/builder<br/>skill and similar_to edges computed on read"]
        seedmod["seed.seed_demo<br/>the ten-document demo profile · zero Gemini calls"]
        ident --> routes
        routes --> parser
        routes --> scraper
        routes --> typed
        routes --> cat
        routes --> ragm
        routes --> career
        routes --> router
        routes --> embed
        routes --> builder
        routes --> seedmod
        ocr --> vis
        builder --> rel
        builder --> embed
        seedmod --> embed
    end

    subgraph stores["Storage on the API host · ephemeral on the free tier"]
        files[("uploads/user_id/<br/>originals, byte-for-byte<br/>+ .meta.json sidecar")]
        sql[("SQLite · source of truth<br/>documents · entities · tags · career_paths")]
        chroma[("ChromaDB · derived cache<br/>rebuildable from raw_text")]
    end

    client -- "HTTPS · CORS allowlist" --> ident
    limiter -- "5 per minute · 20 per day" --> gemini[["Gemini 3 Flash<br/>gemini-3-flash-preview"]]
    guard -- "GitHub REST · public web" --> internet[["The internet"]]
    routes -- "save_original, checksum verified" --> files
    files -. "read only, never written again" .-> parser
    routes --> sql
    builder --> sql
    seedmod --> sql
    embed --> chroma
    sql -. "startup sync · incremental;<br/>full rebuild only if the store won't open" .-> chroma

    classDef ext fill:#f2e8d8,stroke:#8a7355,color:#2f2620
    classDef store fill:#e7edf4,stroke:#5b6b7f,color:#22303f
    class gemini,internet ext
    class files,sql,chroma,pre store
```

Four things in that picture carry most of the design:

- **The original is written once and only ever read afterwards** (the dashed
  arrow back out of `uploads/`). Everything the AI produces lands somewhere else,
  which is what makes re-extraction and integrity verification possible at all.
- **Four Gemini callers, one rate limiter.** The free-tier budget is per *key*,
  not per module. Embeddings deliberately sit outside it: they run locally, cost
  nothing, and must stay fast.
- **`query_router` sits between the routes and the vector store, and never calls
  Gemini.** Query *understanding* is deterministic; the paid model is reserved
  for answer *synthesis*. See the retrieval diagram below.
- **`precomputed` short-circuits the embedding model.** Local embedding is free
  but not fast, and the deploy target has a fraction of a dev machine's CPU. Both
  callers of `embed_texts` — `add_document`'s chunk windows and the whole-`raw_text`
  queries `graph/builder` issues on *every* graph read — hit the shipped table
  first. Keys are the SHA-256 of the exact string, so a stale table degrades to
  slow, never to wrong.

---

## Ingestion — how a document gets in

Every input converges on the same text → categorize → store → embed spine, so a
typed sentence and a scanned certificate become the same kind of record.

```mermaid
flowchart TB
    up["File upload"] --> save["storage.save_original<br/>SHA-256 computed and re-verified on write"]
    save --> ext["file_parser.extract_text"]
    ext --> layer{"Usable text layer?"}
    layer -- "yes" --> text["extracted text"]
    layer -- "no, or too short" --> tess{"Tesseract present<br/>and produced text?"}
    tess -- "yes · free, no quota" --> text
    tess -- "no" --> vision["Gemini Vision OCR · 1 call<br/>verbatim transcript only, never a description"]
    vision -- "transcribed" --> text
    vision -- "nothing legible" --> stuck["raw_text left empty<br/>+ structured reason + retryable flag<br/>POST /documents/id/reextract retries later"]

    urlin["Pasted URL"] --> guard["url_guard<br/>scheme · public IPs · every redirect hop · 5 MB"]
    guard --> scrape["github_scraper · web_scraper"]
    scrape --> text
    typed["text_entry · a typed achievement<br/>no file at all"] --> text
    seed["seed.seed_demo<br/>the demo profile — enters below the AI steps"] --> row

    text --> catg["categorizer · 1 call<br/>type · category · title · date · summary<br/>skills · organizations · people · tags"]
    catg -- "quota, timeout, no key,<br/>unparseable answer" --> deg["filename-based guess, confidence 0.0<br/>+ degraded_reason + retryable<br/>the upload is never lost"]
    catg --> row[("SQLite row<br/>+ entity and tag rows")]
    deg --> row
    row --> emb["embeddings.add_document<br/>~900-char windows, 150 overlap, title prepended<br/>capped at 40 chunks — a long document is truncated"]
    emb --> pre{"Shipped vector for this exact text?<br/>ai/precomputed, keyed by SHA-256"}
    pre -- "hit · the demo profile" --> vec[("ChromaDB")]
    pre -- "miss" --> model["MiniLM-L6-v2 via ONNX<br/>local, free, unlimited — but not fast"]
    model --> vec

    classDef gem fill:#fbeccd,stroke:#a4770a,color:#3a2c10
    classDef store fill:#e7edf4,stroke:#5b6b7f,color:#22303f
    class vision,catg gem
    class row,vec store
```

Amber marks the two steps that spend from the **20-calls-per-day** budget — the
constraint the whole design is shaped around. A text-layer PDF costs one call; a
scan costs two, and ~26s at 13s spacing.

`seed_demo` joins the spine *after* both amber steps, with its categorizations
written as constants and its vectors already shipped. That is what makes "Load
Demo Profile" cost zero calls, and it is why a live demo still works on a key
whose quota is gone.

---

## Retrieval — how a question gets answered

```mermaid
flowchart TB
    q["Query"] --> router{"query_router.route<br/>deterministic — no Gemini call, no latency"}
    router -- "filter · 'show all my certificates'" --> sqlf["SQLite filter<br/>matches category OR document_type"]
    router -- "anything else" --> sem["ChromaDB vector search, top-k"]
    sqlf -- "matched nothing:<br/>fall back, flagged fell_back" --> sem
    sem --> hyd["Hydrate hits from SQLite<br/>re-check user_id on every row"]
    sqlf --> rows["Ranked sources, each linking<br/>to its original file"]
    hyd --> rows
    rows --> ask{"Question-shaped,<br/>or 'Ask AI' clicked?"}
    ask -- "no" --> grid["Results grid — instant, zero quota"]
    ask -- "yes" --> ragc["POST /api/answer → ai/rag · 1 call<br/>grounded in exactly the sources on screen"]
    ragc --> card["Answer card + cited-source badges"]
    ragc -- "degraded" --> sourcesonly["Sources only, no invented answer"]

    classDef gem fill:#fbeccd,stroke:#a4770a,color:#3a2c10
    class ragc gem
```

Query *understanding* is deterministic; Gemini is reserved for answer
*synthesis*. That split is why search stays instant, why a filter query costs no
quota, and why an answer can only ever cite documents the user can already see.
