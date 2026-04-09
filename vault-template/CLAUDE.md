# CLAUDE.md — DjangoCLI Vault Schema

## Who I Am
<!-- Fill in your personal context file and update this pointer -->
Full context: `Context/personal-context.md`

## Vault Architecture — LLM Wiki Pattern

This vault follows the LLM Wiki pattern: a persistent, compounding knowledge base where the LLM maintains the wiki layer. Knowledge is compiled once and kept current, not re-derived on every query.

### Three Layers

**Layer 1 — Raw Sources (`Raw/`)**
Immutable source documents. The LLM reads but NEVER modifies these.
- `Raw/articles/` — web articles, blog posts
- `Raw/transcripts/` — YouTube transcripts, meeting notes
- `Raw/images/` — screenshots, photos, diagrams
- `Raw/data/` — spreadsheets, exports, data files

**Layer 2 — The Wiki (existing folders)**
LLM-generated and LLM-maintained. Your AI assistant owns this layer — creates pages, updates cross-references, keeps everything consistent. You read and direct; the AI writes and maintains.
- `Context/` — personal context, workflow, learnings
- `Projects/` — one file per project with full spec, status, decisions
- `People/` — contacts and collaborators
- `Health/` — health data, supplements, fitness
- `Ideas/` — parked ideas
- `Tools/` — architecture specs, guides
- `Daily Notes/` — session logs by date

**Layer 3 — The Schema (this file)**
This CLAUDE.md tells the LLM how the wiki is structured, what conventions to follow, and what workflows to run.

### Navigation Files
- `index.md` — content-oriented catalog of every wiki page, organized by category
- `log.md` — chronological, append-only record of ingests, queries, and wiki changes

### Wiki Conventions
- All wiki pages use `[[double bracket]]` links for cross-references
- Every wiki page has YAML frontmatter (type, created, updated, tags)
- When new info contradicts existing pages, update the existing page and note the contradiction
- When creating a new page, add links FROM related existing pages TO the new page
- Prefer updating existing pages over creating new ones — compound, don't scatter

## Session Rules
- Start every session by reading the latest session log in `Daily Notes/`
- Before writing code, check the relevant project file for current status
- Log everything meaningful to the session log (commits, fixes, decisions, blockers)
- When done: write/update session log, update context files if anything changed

## Tech Stack
<!-- Update this after running setup.sh -->
- **Bot:** Flask + SendBlue + Claude API (tool-use) on Render
- **Server:** Mac Mini running FastAPI + Tailscale
- **Vault:** Obsidian + Obsidian Sync
- **Code:** Python, GitHub
