# Changelog

## Unreleased - asset graph and resumable research workflows

- Added a canonical asset layer for papers, evidence, notes, documents and team outputs with SHA-256 content hashes, revisions, relations, provenance and FTS5/CJK fallback search.
- Added explicit, idempotent legacy index rebuild support via `POST /api/assets/rebuild`; existing SQLite rows are preserved and receive stable `db://` asset URIs.
- Made Markdown mirrors atomic and revision-aware, including editor notes, workflow documents, citation ledgers and team output files.
- Added persistent workflow runs/nodes, idempotency keys, stale-run recovery and project-scoped cleanup.
- Connected project-space search to the asset index while retaining legacy fallback scans.
- Added team task checkpoints, retry metadata, run IDs, input/output asset links and output provenance.
- Added dependency-aware team scheduling with downstream blocking, structured output contracts and Reviewer approval/return gates.
- Linked paper-bound notes to paper assets, preserved one asset across project rebinds, archived deleted note mirrors and made legacy rebuilds prefer existing Markdown files.
- Made failed workflow requests fully idempotent and surfaced migration failures instead of deferring them to misleading missing-column errors.
- Added execution-boundary tool risk classification for read-only, workspace-write, local-execution, network-read and external-write actions.
- Ranked `paper_search` results by query relevance × citation count (per-source duplicates collapsed, hard cap 100 per search, OpenAlex abstracts rebuilt from the inverted index instead of falling back to the title).
- Verification baseline: Python test suite and desktop web smoke tests pass in the development environment.
