---
name: release-to-github
description: Prepare and publish versioned GitHub releases from a repository, including version metadata, changelog, build artifacts, Git commits and tags, release notes, and asset verification. Use when asked to release, publish a version, create a GitHub release, or upload release files to a remote repository.
---

# Release to GitHub

Prepare a reviewable release, publish it to the repository's configured GitHub remote, and verify the published tag and assets. Use the repository's existing version scheme, build instructions, checks, and release conventions.

## Workflow

1. **Inspect repository state.** Read applicable `AGENTS.md` files and release documentation. Check the current branch, `git status`, remotes, recent tags, the default branch, and existing GitHub Releases. Confirm that `gh` is authenticated and that `origin` points to the intended GitHub repository. Never infer the publish target from an unrelated remote such as an upstream or archived fork.
2. **Resolve the version.** Use the version supplied by the requester. If absent, inspect recent tags and changes to choose the next appropriate SemVer version; ask only when the intended version or compatibility boundary cannot be determined. Check both local and remote refs and existing releases. Never move, overwrite, or reuse an existing version tag.
3. **Review release contents.** Read the changes since the prior release. Update the authoritative version file, user-facing version/download references, and changelog or release notes when the repository uses them. Keep notes factual and limited to shipped behavior. Do not include local databases, credentials, test reports, screenshots, or temporary scripts in commits or assets.
4. **Run release checks.** Follow project build instructions and run documented checks suitable for the change. Build requested release artifacts from the exact source revision intended for the tag. Verify expected files and sizes, inspect archives for the expected executable and directory structure, and calculate SHA-256 hashes. Do not label an old build as a new release.
5. **Review and commit.** Inspect the full staged diff and `git diff --cached --check`. Stage only release source changes and intended tests or documentation. Create a focused commit. Do not stage all files blindly, discard user changes, amend commits, or rewrite shared history.
6. **Publish Git refs.** Fetch the target remote and confirm the release commit descends from the current remote default branch. If the remote advanced, integrate those changes safely and rerun affected checks. Push the branch with a normal push. Create an annotated `v<version>` tag at the release commit and push that tag. Never force-push.
7. **Publish the GitHub Release.** Write release notes to a file and call `gh release create` for the exact tag, attaching only the verified release assets. Use a stable, descriptive title and include asset hashes when useful. Keep the release public unless the requester or repository convention specifies otherwise.
8. **Verify remotely.** Read the release back with `gh release view --json tagName,name,url,isDraft,isPrerelease,assets,body`. Confirm the tag resolves to the intended commit, the release is published, every expected asset is uploaded, and each remote digest matches the local SHA-256. Check the remote branch and final worktree state.

## Safety and recovery

- Treat an explicit request to release a version as authorization to push its reviewed commit and publish the corresponding GitHub Release. Do not ask again for permission already given in the conversation.
- Stop before committing or publishing if the intended repository is ambiguous, the release tag already exists, user changes cannot be safely separated, required checks fail, or build artifacts do not match the source revision. Explain the concrete blocker and preserve the worktree.
- If a push is rejected, fetch and inspect the new remote history; integrate it without force and rerun affected checks.
- If GitHub release creation or upload fails, inspect whether a partial release exists before retrying. Resume or repair that same release without deleting or replacing a published release or tag.
- Keep build output, release notes, and checksums in ignored or temporary paths unless the repository intentionally versions them.

## This repository: 览籍智研

- Read the app version from `VERSION`; update the download entry in `README.md` and add a dated entry to `doc/CHANGELOG.md`.
- Build Windows desktop files with Python 3.10 and the pinned `requirements.txt`: run PyInstaller on `tools/览籍智研.spec`, then `tools/installer.spec`. The regular build is `dist/览籍智研/`; the installer is `dist/览籍智研-安装程序.exe`.
- Create `LanjiZhiyan-<version>-Portable.zip` containing the complete `dist/览籍智研/` directory and `LanjiZhiyan-<version>-Setup.exe` copied from the fresh installer. Store release artifacts in ignored `dist/release/`.
- Smoke-check the portable executable with an isolated `LANJI_ZHIYAN_HOME` and an unused local port; verify the app serves HTTP 200, then stop only the process started for that check.
- Include only the two versioned assets in the GitHub Release. Do not upload the local test-report directory or temporary verification scripts.
