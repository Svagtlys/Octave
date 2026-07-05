---
name: create-release
description: Use when creating a new Octave release — minor/major version bumps, tagging, merging develop to main, cutting release branches, triggering Docker image publishes to GHCR, or merging hotfixes. Triggers when the user says "release", "ship", "tag a version", "bump version", "publish Docker images", "hotfix", or "merge hotfix".
---

# Create Release

## Overview

Create a new Octave release by merging `develop` into `main`, tagging the version, and triggering the automated Docker publish workflow. Also handles hotfix merges.

**Core principle:** Releases are created by tagging `main`. Everything else (tests, migrations, builds) must pass before tagging.

## When to Use

- User says "release version X.Y.Z", "ship v1.2.0", "tag a new release"
- User says "bump to version X.Y", "publish Docker images"
- Feature work on `develop` is complete and ready for release
- Preparing a minor or major release
- Merging hotfix PRs into main, release branch, and develop

**Do NOT use when:** Starting a hotfix branch (use `start-work-item` with `hotfix` label instead).

## Versioning Rules

Octave uses [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`.

| Increment | When | Example |
|---|---|---|
| `PATCH` (1.0.**1**) | Bug fix, no new behavior | `1.0.1` |
| `MINOR` (1.**1**.0) | New feature, backward compatible | `1.1.0` |
| `MAJOR` (**2**.0.0) | Breaking change (API, schema, config) | `2.0.0` |

## Branch Model

| Branch | Purpose |
|---|---|
| `main` | Latest tagged release. **Never commit directly.** |
| `develop` | Integration branch for next release. All features merge here. |
| `release/x.y` | Archival branch per shipped minor version. Cut from `main` at first `x.y.0` tag. |

## Pre-Release Checklist

**Run these commands on `develop` BEFORE creating the release PR:**

```bash
# 1. Ensure develop is up to date
git checkout develop
git fetch origin
git merge origin/develop

# 2. Run all tests (MUST pass, including INTEGRATION)
backend/.venv/bin/pytest

# 3. Lint check (MUST be clean)
backend/.venv/bin/ruff check .

# 4. Frontend builds (MUST succeed)
cd frontend && npm run build

# 5. Verify no uncommitted changes
git status
```

### If Any Step Fails

Create a fix branch from `develop`, resolve the issue, and open a PR into `develop` titled `chore: prep for release v<X.Y.Z>`. Follow the [`create-work-item`](.agents/skills/create-work-item/SKILL.md:1) skill to track the work item if the fix is non-trivial.

```bash
git checkout -b chore/prep-release-v<X.Y.Z>
# fix the issue(s)
git commit -m "chore: fix <issue> for v<X.Y.Z> release"
git push origin chore/prep-release-v<X.Y.Z>
gh pr create --base develop --title "chore: prep for release v<X.Y.Z>"
```

Wait for the PR to be reviewed and merged into `develop` before proceeding.

## Release Flow (Minor/Major)

### Step 1: Create Release PR (`develop` → `main`)

Once the pre-release checklist passes on `develop`, open a PR to merge into `main`:

```bash
gh pr create --base main --head develop --title "release: v<X.Y.Z>" --body "Release v<X.Y.Z>

- [x] All tests passing (including integration)
- [x] Lint clean
- [x] Frontend builds successfully
"
```

**Merge settings:** Use **"Create a merge commit"** (not squash, not rebase). This is equivalent to `--no-ff` and preserves the merge commit for traceability.

Wait for CI to pass, then merge the PR.

### Step 2: Tag the Release

After the PR is merged, tag `main`:

```bash
git checkout main
git fetch origin
git tag -a v<X.Y.Z> -m "Release v<X.Y.Z>"
git push origin v<X.Y.Z>
```

**Tag format:** `v` prefix followed by semver (e.g., `v1.1.0`, `v2.0.0`).

Pushing the tag triggers the [publish workflow](.github/workflows/publish.yml:1) automatically.

### Step 3: Verify Docker Publish

The tag push triggers `.github/workflows/publish.yml` which:

1. Builds **backend** multi-arch (`linux/amd64`, `linux/arm64`)
2. Builds **frontend** (`linux/amd64`)
3. Tags both with version (e.g., `1.1.0`) and `latest`
4. Pushes to GHCR (`ghcr.io/svagtlys/octave-backend`, `ghcr.io/svagtlys/octave-frontend`)

Monitor the workflow run:

```bash
gh run list --workflow=publish.yml --limit 1
gh run view --log
```

### Step 4: Cut `release/x.y` Branch (First Release of Minor)

For the **first** release of a minor version (e.g., `1.1.0`), cut the archival branch:

```bash
git branch release/<X.Y> v<X.Y.Z>
git push origin release/<X.Y>
```

This branch is append-only with hotfixes. Future hotfixes for this minor version will branch from `main` and merge into `release/x.y`.

## Hotfix Flow

Merge approved hotfix PRs, tag a patch release, and backport fixes to develop.

### Step 1: Resolve the Version

Ask the user: "What patch version are you releasing? (e.g. 1.0.1)"

Strip any leading `v` — work with bare semver. The git tag will be `v<version>`.

### Step 2: List Hotfix PRs Ready to Merge

```bash
gh pr list --base main --state open --json number,title,headRefName,reviewDecision,url
```

Show the list to the user. For each PR, note whether it is approved or still pending review.

If any PRs are not yet approved, ask: "These PRs are not yet approved: [list]. Merge the approved ones now, or wait for all?" — **wait for the user's answer before continuing.**

### Step 3: Verify Main is Clean and Up to Date

```bash
git fetch origin
git checkout main
git status
git log origin/main..HEAD --oneline
```

- Confirm the working tree is clean
- Confirm local main matches `origin/main`

If either check fails, **stop and tell the user** — do not proceed.

### Step 4: Determine the Active Release Branch

Check whether a `release/<major>.<minor>` branch exists for the version being patched:

```bash
git branch -r | grep "release/$(echo <version> | cut -d. -f1-2)"
```

- If it exists (e.g. `release/1.0`), hotfixes go to **both** `main` and `release/<major>.<minor>`.
- If it does not exist, hotfixes go to `main` only (release branch will be cut when the minor version ships).

### Step 5: Confirm the Plan with the User

Show the user exactly what will happen:

```
Hotfix release plan for v<version>:

  PRs to merge into main (no squash):
    - #<N> <title> (hotfix/<branch>)
    ...

  Also merge into release/<major>.<minor>:   ← only if branch exists
    - same PRs (keeps release branch current for archival)

  Then:
    git tag v<version>
    git push origin main
    git push origin release/<major>.<minor>  ← only if branch exists
    git push origin v<version>               ← fires the GHCR publish workflow

  Backport each hotfix branch to develop:
    git checkout develop
    git merge --no-ff hotfix/<branch>   (repeated for each PR)
    git push origin develop
```

**Wait for explicit user approval before running any git commands.**

### Step 6: Merge Each Hotfix PR into Main

For each approved PR, merge it (no squash) directly via the branch:

```bash
git checkout main
git merge --no-ff hotfix/<branch> -m "chore(hotfix): merge hotfix/<branch> into main"
```

If a merge has conflicts, **stop and tell the user** — do not attempt to resolve them automatically.

After all merges:

```bash
git log --oneline -5
```

Show the user the resulting main commit history.

### Step 7: Merge Each Hotfix PR into `release/<major>.<minor>` (if branch exists)

```bash
git checkout release/<major>.<minor>
git pull origin release/<major>.<minor>
```

For each hotfix branch:

```bash
git merge --no-ff hotfix/<branch> -m "chore(hotfix): merge hotfix/<branch> into release/<major>.<minor>"
```

If a merge has conflicts, **stop and tell the user** — do not proceed.

### Step 8: Tag and Push

```bash
git tag v<version>
git push origin main
git push origin release/<major>.<minor>  # only if branch exists
git push origin v<version>
```

Confirm the publish workflow fired:

```bash
gh run list --workflow publish.yml --limit 3
```

Show the user the Actions run URL.

### Step 9: Backport Each Hotfix Branch to Develop

```bash
git checkout develop
git pull origin develop
```

For each hotfix branch:

```bash
git merge --no-ff hotfix/<branch> -m "chore(hotfix): backport hotfix/<branch> to develop"
```

If a backport has conflicts, **stop and tell the user** — do not proceed with remaining backports until this is resolved.

After all backports:

```bash
git push origin develop
```

### Step 10: Finish

Report to the user:

```
✓ Hotfix PRs merged into main
✓ Hotfix PRs merged into release/<major>.<minor>   ← only if branch existed
✓ main tagged v<version>
✓ GHCR publish workflow triggered
✓ Hotfixes backported to develop

Next steps:
- Watch the workflow at: https://github.com/Svagtlys/Octave/actions
- Update deploy/docker-compose.yml image tags to v<version> if not already done
- Close any related GitHub issues if not auto-closed by the merged PRs
```

## Common Mistakes

| Mistake | Fix |
|---|---|
| Committing directly to `main` | Merge from `develop` or `hotfix/*` only |
| Forgetting `--no-ff` on merge | Always use `--no-ff` to preserve merge history |
| Tagging before tests pass | Run full checklist before tagging |
| Missing `v` prefix in tag | Tag must be `v1.2.3`, not `1.2.3` |
| Cutting `release/x.y` from `develop` | Cut from `main` at the tag point |
| Merging future features into `release/x.y` | `release/x.y` is append-only with hotfixes only |

## Quick Reference

| Action | Command |
|---|---|
| Run tests | `backend/.venv/bin/pytest` |
| Lint check | `backend/.venv/bin/ruff check .` |
| Frontend build | `cd frontend && npm run build` |
| Merge develop→main | `git merge origin/develop --no-ff -m "chore: merge develop into main for v<X.Y.Z>"` |
| Tag release | `git tag -a v<X.Y.Z> -m "Release v<X.Y.Z>"` |
| Push tag | `git push origin v<X.Y.Z>` |
| Cut release branch | `git push origin release/<X.Y>` |
| Check publish workflow | `gh run list --workflow=publish.yml` |

## Squash Rules

| Merge | Squash? |
|---|---|
| `feature/*` → `develop` | **Yes** |
| `fix/*` → `release/x.y` | **Yes** |
| `develop` → `main` | **No** (`--no-ff`) |
| `hotfix/*` → anywhere | **No** (`--no-ff`) |
| `release/x.y` → `main` | **No** |

## Real-World Impact

Following this process ensures:
- `main` always reflects a working, tagged release
- Docker images are published automatically with correct version tags
- Users can pin to specific versions or use `latest`
- Archival `release/x.y` branches provide exact code for each minor version
- Hotfixes can be applied to both live and development branches simultaneously
