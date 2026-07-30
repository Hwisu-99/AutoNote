# AutoNote

Upload a research paper PDF and get back an Obsidian note, a concept diagram, and a live knowledge graph — automatically.

AutoNote reads a paper, sends it to Claude for a structured summary, and writes the result straight into your Obsidian vault as a Markdown note plus an Excalidraw concept diagram. The generated note is also uploaded to Supabase Storage, and a web UI renders an Obsidian-style force-directed graph of your notes, tags, and concepts.

## How it works

1. Drop a PDF into the web UI.
2. The backend extracts the text (PyMuPDF) and sends it to Claude with a structured JSON schema (title, summary, problem/gap/method, key concepts, relationships between concepts, tags, ...).
3. Claude's response is used to write:
   - `<vault>/AutoNote/<slug>/<slug>.md` — the note itself, including wikilinks (`[[...]]`) between extracted concepts so they show up as graph nodes
   - `<vault>/AutoNote/<slug>/<slug>.excalidraw` — a concept diagram built from the same concepts/relationships
4. The `.md` note (only the note — not the PDF or the diagram) is uploaded to Supabase Storage.
5. The web UI shows:
   - A sidebar of every paper stored in Supabase, each with a button to focus the graph on just that paper
   - A d3-force graph view: papers as orange nodes, tags as green nodes, concepts as gray nodes, connected by wikilinks and shared tags — the same logic Obsidian's graph view uses
   - The estimated USD cost of the Claude API call, computed from actual token usage and the model that served the request (not hardcoded to one model)

## Requirements

- Python 3.11+
- An Obsidian vault on disk
- An [Anthropic API key](https://console.anthropic.com/)
- A [Supabase](https://supabase.com/) project with a Storage bucket

## Setup

```bash
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

pip install -r requirements.txt
copy .env.example .env       # Windows
# cp .env.example .env       # macOS/Linux
```

Fill in `.env`:

```
ANTHROPIC_API_KEY=your-anthropic-api-key
OBSIDIAN_VAULT_PATH=C:\path\to\your\Obsidian\vault
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-supabase-service-role-key
SUPABASE_BUCKET=autonote-notes
```

`SUPABASE_KEY` should be a `service_role` key since notes are uploaded from trusted server-side code — never expose this key client-side.

## Run

```bash
uvicorn app:app --reload --port 8123
```

Open `http://localhost:8123`.

## Project structure

```
app.py                        # FastAPI app: upload pipeline + /api/graph + /api/papers
paper_notes/
  extractor.py                 # PDF -> text (PyMuPDF)
  claude_client.py              # Claude call, structured summary, per-model cost calculation
  obsidian_writer.py            # writes the Markdown note (incl. concept wikilinks)
  excalidraw_writer.py          # writes the .excalidraw concept diagram
  graph_builder.py              # scans the vault and builds graph nodes/edges
  dedup.py                      # MinHash-based near-duplicate merging for concept/entity labels
  supabase_writer.py             # Supabase Storage upload/list
  utils.py                       # slugify, etc.
static/
  index.html, graph.css, graph.js, papers.js   # frontend: upload UI, graph view, paper sidebar
test_supabase_upload.py        # standalone script to verify Supabase connectivity
backfill_supabase.py            # one-off script to upload pre-existing vault notes to Supabase
```

## Notes on cost

Claude API pricing is looked up per-model in `paper_notes/claude_client.py` (`_PRICE_PER_MTOK`), keyed by the exact model ID that served the response — so cost reporting stays accurate even if the configured model changes. If you switch to a model not yet in that table, adding it there is required (a `ValueError` is raised instead of silently reporting the wrong price).
