# Project guidance

## Product and architecture

- Lanji Zhiyan is a local-first Windows research workspace. The desktop entry point is apps/desktop/server.py; the browser UI is in apps/desktop/web/; Python services are grouped under packages/.
- User data includes the SQLite database, project files, notes, PDFs, attachments, and credentials. Never remove or reset data under data/, %LOCALAPPDATA%/览籍智研, output/, or data.old-* as part of code or documentation cleanup. Treat existing uncommitted changes as user work and preserve them.
- Add database schema changes as forward-only numbered migrations under migrations/. Keep existing databases upgradeable.
- Before removing code or assets, search imports, route registration, UI references, packaging configuration, and tests.

## Documentation

- README.md is the short project overview and installation entry point.
- Write all plan documents under the repository-root Plan/ directory. This includes implementation plans, design plans, roadmaps, migration plans, and verification plans. Create and update plans there rather than in the repository root, documentation directories, or temporary task directories.
- docs/ is exclusively for project usage instructions and their supporting assets. This repository currently uses doc/; the same restriction applies to doc/. Do not put plans, design documents, release histories, diagnostic reports, audit notes, or test evidence in either usage-documentation directory.
- doc/使用指南.md is the Chinese user-guide index; detailed instructions live in doc/guide/. Update the relevant module when behavior changes, and keep the index current when modules or navigation change.
- Keep release/change history outside the usage-documentation directories; use the repository-root CHANGELOG.md for new release entries.
- doc/screenshots/ contains images referenced by project documentation.
- Record lasting usage information in the relevant guide module. Keep temporary diagnostic reports, audit notes, and test evidence in an ignored task directory outside Plan/, doc/, and docs/.
- Keep documentation links relative to the repository and remove references when their target is retired.

## Development and verification

- Run the source app with tools/start.bat; use tools/check.bat and the relevant Python or browser checks after code changes.
- Build Windows packages with tools/build-app.bat after installing requirements-build.txt.
- Keep secrets, local databases, generated reports, and build outputs out of commits. Do not delete ignored files solely because Git does not track them; first identify whether they are caches, user data, or current verification artifacts.
