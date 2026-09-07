<!-- Reviewer worksheet from the 2026-09-03 public-sector readiness review. Raw evidence with file:line citations; the consolidated position is docs/PUBLIC_SECTOR_REVIEW_2026-09.md, which supersedes this file where they disagree (e.g. the w3id PURL resolves since 2026-08-19). Tree reviewed: main @ c96577d50c. -->

# Governance, rights, and CI — adoption readiness for the Estonian public sector

Reviewer scope: `.github/**`, packaging/repo config, licence + rights + GDPR docs,
governance/process docs, CHANGELOG, stale planning docs, `work-overview.html`,
git cadence, live GitHub state. Repo: `/Users/henrikaavik/progemoge/estleg-w3id`,
HEAD `c96577d50c` on `main`.

## Scope & method

Read every file in scope at `file:line` granularity. Then queried live GitHub
state read-only with `gh` (issues, PRs, Actions runs and failing job logs,
branch protection, code-scanning alerts, Dependabot security settings, release
assets, workflow token permissions) and `git log` for authorship and cadence.
No repository file was modified.

The decisive evidence is not in the files. It is in the Actions tab: the
repository's own validation workflow has failed on **every run on `main` since
2026-08-19**, including the release commit itself. That single fact undercuts
the "CI a public body could reproduce" criterion and contradicts the
maintainer's own status page.

---

## Adoption-readiness scorecard

### Data rights — 3/5

**Evidence.** The layered model is genuinely well-reasoned and, unusually,
honest. `LICENSE:1-17` prepends a scope note that MIT covers software only.
`NOTICE:22-29` separates layer (a) third-party texts from layer (b) the
project's compilation. `docs/DATA_RIGHTS.md:71-86` correctly distinguishes
"§ 5 Autoriõiguse seadus means the statutory *text* is copyright-free" from
"the consolidated Riigi Teataja *product* and the sui generis database right
may still carry terms" — a distinction most open-legal-data projects get wrong.
`docs/DATA_RIGHTS.md:105` correctly names Decision 2011/833/EU and the EuroVoc
attribution requirement.

**Against.** Five open VERIFY items (`docs/DATA_RIGHTS.md:121-125`) are the
five questions a procurement lawyer will actually ask, and none is answered.
The file has not been touched since `6b326c1b2d` (2026-06-29) — 66 days — while
`docs/DATA_PROTECTION.md` moved on 2026-08-19. The rights position is therefore
a stated intent, not a cleared position, and both `NOTICE:4-10` and
`docs/DATA_RIGHTS.md:3-7` explicitly say **do not redistribute on the strength
of this document**. Yet the v1.0.0 release publishes twelve corpus assets.

### GDPR / data protection — 2/5

**Evidence.** `docs/DATA_PROTECTION.md` is the strongest doc in scope. It
identifies the right hard problem (`:24-29`: KarS charges attached to
identifiable persons engage Article 10 GDPR), refuses to overclaim
(`:49-52`: the DPO must confirm a 6(1)(e)/(f) basis is even available to a
non-authority project), and warns downstream reusers they become independent
controllers (`:54-63`).

**Against — the blocking issue.** The critical re-identification check is still
an unticked box (`docs/DATA_PROTECTION.md:71-77`): nobody has confirmed whether
the ~12,137 stored Riigikohus names match RIK's official *anonymised* feed or
exceed it. The doc says that if they exceed it, "redaction is required before
any publication". Publication has already happened: `curia_combined.jsonld.gz`
is a live v1.0.0 release asset and `docs/DATA_PROTECTION.md:20` records that
CURIA party names sit in `rdfs:label` for ~22,290 records. The release bundle
contains no `NOTICE`, no `DATA_RIGHTS.md`, and no `DATA_PROTECTION.md` —
verified by `gh release view v1.0.0`: the twelve assets are graph dumps, shapes,
catalog, manifest and `SHA256SUMS` only. Personal data has shipped without its
notice attached.

Also missing entirely: a named controller, a data-subject contact route, an
erasure/objection procedure (flagged as TODO at `:84-85`), retention, and any
DPIA. `w3id/estleg/.htaccess:6` makes a personal Gmail address the only contact
on the namespace a data subject would follow.

### Licence — 2/5

**Evidence.** GitHub's own licence detector reports `{"key":"other","name":
"Other"}` for this repository. The cause is the 19-line SCOPE NOTE prepended
above the MIT text at `LICENSE:1-19`: the note is legally right and
machine-hostile. Automated licence scanners (the tooling a procurement or
security team runs) will not classify this repo, and SPDX identifiers appear
nowhere.

**Direct contradiction.** `CITATION.cff:2-21` declares `type: dataset` and
`license: CC-BY-4.0` — an unqualified CC BY offer over the whole dataset. That
is exactly the claim `NOTICE:114-116` and `docs/DATA_RIGHTS.md:52` say the
project is *not* in a position to make, because CC BY 4.0 covers layer (b)
only. `CITATION.cff` is the machine-readable file Zenodo, GitHub's cite widget
and re-use crawlers consume, so the retracted overclaim survives in the one
place a machine will read it.

`CITATION.cff:26-27` also advertises `https://w3id.org/estleg/1.0.0` as the
version IRI while noting registration is pending; per issue #516 that URL
still 404s.

### Sustainability / bus factor — 1/5

**Evidence.** `git log --format='%an' | sort | uniq -c` returns exactly one
line: **352 commits, Henrik Aavik**. There has never been a second committer.
Monthly cadence is bursty and already shows two stalls:

| Month | Commits |
|---|---|
| 2026-02 | 12 |
| 2026-03 | 109 |
| 2026-04 | 7 |
| 2026-05 | 84 |
| 2026-06 | 71 |
| 2026-07 | 0 |
| 2026-08 | 69 |

`.github/CODEOWNERS:12-38` assigns `@henrikaavik` to every line, including the
eight safety-critical legal-data paths. So the "mandatory legal-correctness
review" that `CONTRIBUTING.md:50-74` builds an entire process around is, today,
self-review by the sole committer. The file honestly says so at
`.github/CODEOWNERS:7-9` ("When a dedicated legal-domain reviewer joins…"), but
that reviewer does not exist, `main` has no branch protection (`gh api
.../branches/main/protection` → 404 "Branch not protected"), and nothing
enforces the gate.

No file in the repository names an institutional home, a successor maintainer,
a funding source, or a transfer plan. There is no `SECURITY.md`, no
`CODE_OF_CONDUCT.md`, and no `GOVERNANCE.md`. `TEAM_COLLABORATION.md` is the
closest thing to a governance document and it is unusable by an outsider: it
was last touched 2026-02-25, its normative reference at line 5 is
`~/.openclaw/shared/TEAM_COLLABORATION_PROTOCOL_V2.md` (a private local path no
external reader can open), and it mandates twice-weekly retrospectives written
to `memory/YYYY-MM-DD.md` — a directory `.gitignore:59-61` excludes from the
repo. `RETROSPECTIVE_TEMPLATE.md` (2026-02-23) is its unused companion.

### Security posture — 2/5

**What exists.** `.github/workflows/codeql.yml` runs CodeQL on push, PR and a
weekly cron, with correctly minimal `permissions:` (`:15-17`).
`.github/dependabot.yml` covers pip at `/` and `/mcp_server` plus
github-actions, weekly. Default workflow token is read-only
(`default_workflow_permissions: "read"`). Release assets carry `SHA256SUMS`.
For a solo project that is above average.

**Gaps, all verified live.**

1. **Dependabot security alerts are switched off.**
   `gh api .../vulnerability-alerts` → `404 "Vulnerability alerts are
   disabled."`, and `.../automated-security-fixes` → `{"enabled": false}`.
   Version-bump PRs arrive; CVE alerts do not. This is the single cheapest fix
   in the whole review and it is a standard due-diligence checkbox.
2. **Two open CodeQL alerts, unactioned for 16 days.** Alert #1,
   `py/incomplete-url-substring-sanitization`, **high** severity,
   `tests/test_generate_court_decisions.py:299`, opened 2026-08-18. Alert #2,
   `py/overly-large-range`, medium, `examples/quickstart.py:84`. Both are in
   non-shipped paths so real risk is low, but they sit visible in the Security
   tab that a reviewing body will open first.
3. **Actions pinned to mutable major tags**, not commit SHAs —
   `actions/checkout@v4` (`validate.yml:53`, and eight more),
   `github/codeql-action/init@v3` (`codeql.yml:20`),
   `dorny/paths-filter@v3` (`validate.yml:57`). SHA pinning is the OpenSSF
   Scorecard / SSDF expectation for public-sector supply chains.
4. **No top-level `permissions:` block in `validate.yml`** — it relies on the
   repository-level default. Correct today; silently escalates if that setting
   is ever changed.
5. **No `SECURITY.md`**, so there is no coordinated-disclosure route. A
   researcher who finds a defect in a legal dataset has only a personal Gmail.
6. **Eight Dependabot PRs open and stuck** since 2026-08-18 (#668–#675),
   including `github/codeql-action` 3→4 and `actions/checkout` 4→7. They are
   unmerged because required CI is red (below), so the security automation that
   does exist is jammed by the CI failure.

### CI reproducibility — 2/5

The workflow *design* is the best-engineered artifact in my scope. It splits
docs-only PRs from the heavy path (`validate.yml:3-6, 49-79`), buckets SHACL
into a seven-way matrix with `fail-fast: false` and a 30-minute timeout
(`:280-304`), and — genuinely sophisticated — verifies after every `git lfs
pull` that the file is not still a pointer, failing with a quota-specific
message (`:239-244`, `:322-326`, `:359-364`).

**But `main` is red and has been for two weeks.** `gh run list --workflow=
"Validate Ontology" --branch main` returns **failure for all twelve most recent
runs**, from `32217127962` (2026-08-19) through the scheduled run
`33391090500` (2026-08-31). Every v1.0.0 release commit failed CI. Three
distinct root causes, all reproducible from the logs:

**(a) Unpinned linter — the pin was applied to the wrong file.**
`pyproject.toml:48-51` deliberately holds ruff to the historical
`select = ["E4","E7","E9","F"]`, with a comment explaining that "Ruff 0.16
broadened implicit defaults; this repo is not on that ruleset."
`mcp_server/pyproject.toml:40-42` declares its own `[tool.ruff]` with
`line-length` and `target-version` but **no `[tool.ruff.lint] select`**. Ruff
resolves config hierarchically per file, so `mcp_server/**` gets ruff's *own*
current defaults. Combined with the floating dependency `ruff>=0.6,<1`
(`pyproject.toml:20`), a newer ruff landed new default rules and the lint job
now fails with 7 errors, all in `mcp_server/estleg_mcp/data.py` (lines 297,
821, 982, 1175, 1398, 1451, 1576 — `FURB`-class `removeprefix` suggestions).
Nothing in the repository changed; the toolchain did.

**(b) Five tests read Git-LFS artifacts without the `corpus` marker.**
`tests/conftest.py:23-62` implements exactly the right guard: `@pytest.mark.
corpus` auto-skips unless opted in, and `validate.yml:271-278` opts in from the
LFS-materialised job. But the default `pytest` job does no `git lfs pull`, and
these five unmarked tests read LFS-backed files directly, so they parse the
pointer text:

- `tests/test_issue_379_map_preference.py:46` → `oiguskantsler_seisukohad.jsonld`
- `tests/test_issue_417_eurlex_combined.py:64` → `eurlex_combined.jsonld`
- `tests/test_issue_459_annotations.py:17` → `oiguskantsler_seisukohad.jsonld`
- `tests/test_issue_435_ontology_typing.py:120` → `combined_ontology.jsonld`
- `tests/test_issue_557_harmonisation_drafts.py:30` → `combined_ontology.jsonld`

Three fail as `json.decoder.JSONDecodeError: Expecting value: line 1 column 1`
(the LFS pointer header), two as false-negative assertions (`assert 0 > 0`,
`assert -1 >= 0`) because the pointer parses to an empty graph. A sixth,
`tests/test_migrate_uris.py::TestRealMigrationStateFile::
test_apply_command_recognises_already_migrated_when_in_sync`, fails on
execution-order state ("Dry-run report not found"). CI totals:
`6 failed, 4017 passed, 72 skipped`.

**(c) The `estleg-mcp` job fails on a clean checkout.** Job 99484636090:
`assert "karistusseadustik" in slugs` fails with a slug set containing only
ratification-convention laws; downstream `assert len(provs) > 10` → `assert
0 > 10`, and `rt_url` assertions fail. The MCP tests cannot locate the corpus
the way the job assumes.

**Documented-vs-actual gap.** `work-overview.html` §3 states
"4033 passed, 62 skipped" and "`ruff check` green (last recapture md5
7b1c11bb)". CI on the same tree reports 7 ruff errors and 6 test failures. The
page is candid elsewhere — §3 openly admits `validate_all` / SHACL / Seadusloome
were not all green — but the ruff and pytest claims do not survive contact with
the Actions tab. Anyone doing due diligence checks that tab first.

**Version drift.** `requires-python = ">=3.11"` (`pyproject.toml:5`), ruff
`target-version = "py311"` (`:46`), CI runs a single Python `'3.12'` (nine
occurrences in `validate.yml`), and the local venv is **Python 3.14.3**. Three
versions, no matrix; 3.11 and 3.14 are declared or used but never tested.
The runners also warn that `actions/checkout@v4` and `actions/setup-python@v5`
target the deprecated Node 20.

### Repo hygiene — 3/5

**Clean.** `.gitignore` is thorough and well-commented (LFS-adjacent derived
artifacts at `:32-45`, release-only dumps at `:47-50`, agent scaffolding at
`:55-61`). Nothing bad is tracked: `git ls-files | grep -i 'DS_Store'` and the
same for `egg-info` both return nothing, despite four `.DS_Store` files and
`estonian_legal_ontology.egg-info/` sitting on disk. `.gitattributes` correctly
declares the seven LFS artifacts and normalises line endings.

**Untidy.**

- `work-overview.html` (28 KB) is untracked at the repo root — the sole entry
  in `git status`. It is a high-quality August 2026 briefing with real value,
  but as an untracked root-level HTML file it is invisible to clones and
  reads as debris in the working tree.
- Four stale planning docs are presented as current: `ONTOLOGY_ANALYSIS.md`
  (root) carries its own "Historical 2026-02-28 snapshot. Not current" banner at
  `:3` yet still occupies a root filename; `docs/OPEN_ISSUE_VALIDATION_2026-06.md`
  (last touched 2026-06-11) is a point-in-time audit of issues #270–#404;
  `docs/REGULATIONS_INTEGRATION_PLAN.md` (2026-06-01) is a stack of five dated
  "Status" blocks (`:3-36`) rather than a current statement;
  `docs/INTEGRATION_IDEAS.md:1-4` says outright "This file is a historical
  checklist, not an open backlog".
- `docs/superpowers/` (14 files, 9 plans + 5 specs) is agent-workflow
  scaffolding, not project documentation. Its plans open with
  "**For agentic workers:** REQUIRED SUB-SKILL: Use
  superpowers:subagent-driven-development…"
  (`docs/superpowers/plans/2026-05-03-kov-integration-layer2a.md:3`). It cannot
  simply be deleted: `README.md:470-471`, `docs/REGULATIONS_INTEGRATION_PLAN.md:
  12-36`, `CHANGELOG.md:362`, `docs/NAMESPACE_MIGRATION.md:6,82,140` and the CI
  exclusion at `validate.yml:257` all reference it. It needs relocating with
  redirects, not removing.
- `CHANGELOG.md` (769 lines) follows Keep a Changelog headings
  (`:5,13,271,590,...`) but the body is issue-number engineering prose — e.g.
  `:57-61` "Citations target lõige when named (#512)". It is a good developer
  log and not a consumer-readable release note: a ministry cannot tell from it
  what changed in the *data* they depend on. There are no
  Added/Changed/Deprecated/Removed groupings.
- **No deprecation policy.** `grep -i deprecat` over `docs/RELEASE.md` and
  `docs/STABILITY.md` returns nothing. `docs/STABILITY.md` defines a good
  four-tier predicate contract (`:8-13`) and an `@id` freeze policy (`:18-24`),
  and `docs/RELEASE.md:77-83` defines SemVer bump triggers — but nothing states
  how long a version stays available, what notice precedes a breaking change,
  or whether `owl:deprecated` will be used. A public body integrating this
  needs that before it writes queries against `estleg:` IRIs.
- **The validation-gate command differs in four places**, and the divergence is
  exactly where CI is failing:

  | Source | Ruff paths |
  |---|---|
  | `.github/workflows/validate.yml:103` | `scripts/ src/estleg/ tests/ mcp_server/` |
  | `CONTRIBUTING.md:36` | `scripts/ src/estleg/ tests/ mcp_server/` |
  | `.github/PULL_REQUEST_TEMPLATE.md:13` | `scripts/ tests/ mcp_server/` (drops `src/estleg/`) |
  | `CLAUDE.md:23` | `scripts/ src/estleg/ tests/` (drops `mcp_server/`) |

  A contributor following the PR template or `CLAUDE.md` runs a different gate
  from CI. `CLAUDE.md`'s omission of `mcp_server/` is precisely the directory
  breaking the lint job.

---

## Strengths

1. **The rights model is legally literate and honest.** `NOTICE:22-29` and
   `docs/DATA_RIGHTS.md:38-53` get the two-layer compilation analysis right,
   and `docs/DATA_RIGHTS.md:77-86` correctly separates copyright-free statutory
   *text* from the Riigi Teataja consolidated *product* and sui generis database
   right. Most Estonian legal-data projects assert a flat licence and are wrong.
2. **`docs/DATA_PROTECTION.md` names the hard problem instead of hiding it.**
   `:24-29` identifies the Article 10 criminal-conviction exposure; `:49-52`
   refuses to assert a 6(1)(e) basis a non-authority may not have; `:54-63`
   warns reusers they become independent controllers. That is the posture a DPO
   can work with.
3. **A real legal-correctness review gate is specified.** `CONTRIBUTING.md:50-74`
   plus `.github/CODEOWNERS:14-38` plus the checkbox at
   `.github/PULL_REQUEST_TEMPLATE.md:28-34` define eight safety-critical paths,
   a `needs-legal-review` label, and the right default: remove or mark
   low-confidence rather than guess (`CONTRIBUTING.md:72-74`).
4. **`.github/ISSUE_TEMPLATE/data_correction.md` is exactly the right artifact
   for public-sector feedback.** It demands node IRI, file, property, wrong
   value, correct value **and an authoritative citation** (`:23-28`), and
   auto-labels `needs-legal-review`. A ministry lawyer spotting a wrong sanction
   has a structured route.
5. **CI design quality is high where it works.** Path-filtered docs-only fast
   lane (`validate.yml:49-79`), seven-bucket SHACL matrix with per-bucket
   comments justifying each bucket (`:288-304`), and the LFS-pointer guard that
   distinguishes a quota failure from a data failure (`:239-244`).
6. **`docs/STABILITY.md` publishes a consumer contract**, tiering predicates
   into Stable / Additive / Heuristic / Build-marker (`:8-13`) and warning
   against persisting `estleg:isStubNode` as fact (`:15-16`). Very few datasets
   tell you which of their fields are classifier guesses.
7. **The release is properly formed as far as it goes** — SemVer, a single
   source of truth for the version enforced by a test (`docs/RELEASE.md:24-27`),
   twelve assets with `SHA256SUMS`, and catalog URLs pinned off mutable `/main`
   (`CHANGELOG.md:95-101`).

---

## Weaknesses / risks

| # | Finding | Location | Severity |
|---|---|---|---|
| 1 | `main` CI red on all 12 most recent runs since 2026-08-19, including the release commit | `gh run list --workflow="Validate Ontology" --branch main` | **Critical** |
| 2 | Personal-data release asset shipped while the re-identification check is unresolved, and with no data-protection notice in the bundle | `docs/DATA_PROTECTION.md:71-77`; `gh release view v1.0.0` | **Critical** |
| 3 | Bus factor 1: 352/352 commits one author; CODEOWNERS self-review on all legal-critical paths; no branch protection; no successor or institutional home named | `git log`; `.github/CODEOWNERS:12-38`; branch-protection 404 | **Critical** |
| 4 | `CITATION.cff:21` asserts `license: CC-BY-4.0` over the whole dataset, contradicting `NOTICE:114-116` and `docs/DATA_RIGHTS.md:52` | `CITATION.cff:2-21` | **High** |
| 5 | Dependabot security alerts and automated security fixes both disabled | `gh api .../vulnerability-alerts` → 404; `.../automated-security-fixes` → `enabled:false` | **High** |
| 6 | Ruff `select` pin absent from `mcp_server/pyproject.toml` + floating `ruff>=0.6,<1` → lint breaks on toolchain drift alone | `pyproject.toml:20,48-51`; `mcp_server/pyproject.toml:40-42` | **High** |
| 7 | Five LFS-reading tests lack `@pytest.mark.corpus`, so the non-LFS `pytest` job parses pointer files | the five test files listed above; `tests/conftest.py:23-62` | **High** |
| 8 | All five rights VERIFY items unresolved for 66 days while the data is published | `docs/DATA_RIGHTS.md:121-125` | **High** |
| 9 | GitHub cannot detect the licence (`"key":"other"`); no SPDX identifiers anywhere | `LICENSE:1-19`; `gh repo view --json licenseInfo` | **High** |
| 10 | No `SECURITY.md`; only disclosure route is a personal Gmail | repo root; `w3id/estleg/.htaccess:6` | Medium |
| 11 | 2 open CodeQL alerts (1 high) unactioned 16 days | `tests/test_generate_court_decisions.py:299`; `examples/quickstart.py:84` | Medium |
| 12 | 8 Dependabot PRs jammed open since 2026-08-18 because required CI is red | `gh pr list` #668–#675 | Medium |
| 13 | No deprecation policy anywhere; SemVer bump rules exist but no support window or notice period | `docs/RELEASE.md:77-83`; `docs/STABILITY.md` | Medium |
| 14 | Python version drift: declared `>=3.11`, CI-tested `3.12` only, developed on `3.14.3` | `pyproject.toml:5,46`; `validate.yml` ×9; `.venv/bin/python --version` | Medium |
| 15 | Validation-gate command differs across four files; `CLAUDE.md:23` omits the very directory failing lint | table above | Medium |
| 16 | `TEAM_COLLABORATION.md` is the de-facto governance doc but references a private local path and mandates retros into a gitignored directory | `TEAM_COLLABORATION.md:5,65`; `.gitignore:59-61` | Medium |
| 17 | Actions pinned to mutable major tags, not SHAs; no top-level `permissions:` in `validate.yml`; runners warn Node 20 deprecation | `validate.yml:53` et al.; `codeql.yml:20` | Medium |
| 18 | `CHANGELOG.md` is an engineering log keyed to issue numbers, not consumer-readable release notes | `CHANGELOG.md:57-61` and passim | Medium |
| 19 | Four stale planning docs presented alongside current ones | `ONTOLOGY_ANALYSIS.md:3`; `docs/OPEN_ISSUE_VALIDATION_2026-06.md`; `docs/REGULATIONS_INTEGRATION_PLAN.md:3-36`; `docs/INTEGRATION_IDEAS.md:1-4` | Low |
| 20 | `docs/superpowers/` ships agent-workflow scaffolding in the public repo, referenced from `README.md:470-471` | `docs/superpowers/plans/*.md:3` | Low |
| 21 | `work-overview.html` untracked at repo root | `git status` | Low |
| 22 | `work-overview.html` §3 claims "4033 passed" and "ruff green"; CI reports 6 failed / 7 ruff errors on the same tree | `work-overview.html` §3 vs run 33391090500 | Low |

---

## Improvement ideas

1. **Get `main` green, then protect it.**
   *What:* Fix the three root causes — add `[tool.ruff.lint] select = ["E4","E7","E9","F"]` to `mcp_server/pyproject.toml`; add `@pytest.mark.corpus` to the five LFS-reading tests (or give the `pytest` job `actions/checkout` with `lfs: true`); fix the `estleg-mcp` corpus-location assumption and the order-dependent `test_migrate_uris` case. Then enable branch protection on `main` requiring the `lint`, `pytest`, `mcp-server` and `json-validation` checks.
   *Why it matters:* No public body adopts a dataset whose own release commit fails its own validation. Red CI also currently blocks all eight Dependabot PRs, so it is jamming the security automation too. This is the prerequisite for every other item.
   *Effort:* S. *Impact:* H.
   *Files:* `mcp_server/pyproject.toml`, the five test files, `tests/test_migrate_uris.py`, `.github/workflows/validate.yml`, repo settings.

2. **Pin the toolchain the way the data is pinned.**
   *What:* Constrain `ruff` to a minor range (e.g. `ruff>=0.14,<0.15`) in both `pyproject.toml:20` and `mcp_server/pyproject.toml`; pin every GitHub Action to a full commit SHA with a `# v4.2.2` comment; add a top-level `permissions: contents: read` to `validate.yml`; add a Python matrix `[3.11, 3.12, 3.13]` matching `requires-python`.
   *Why:* A reviewing body asks "can we rebuild this exactly?" Today a floating linter alone can turn the build red with no repository change — which is precisely what happened. SHA-pinned actions are an OpenSSF Scorecard and SSDF expectation for public-sector supply chains.
   *Effort:* S. *Impact:* H.
   *Files:* `pyproject.toml`, `mcp_server/pyproject.toml`, `.github/workflows/validate.yml`, `.github/workflows/codeql.yml`.

3. **Turn on Dependabot security alerts and automated security fixes.**
   *What:* Two repository-settings toggles, plus triage the two open CodeQL alerts (dismiss with justification if genuinely test-only).
   *Why:* Version-update PRs without CVE alerts is the wrong half of Dependabot. "Are security alerts enabled?" and "are code-scanning alerts triaged?" are literal checkboxes on public-sector supplier questionnaires, and both currently answer no.
   *Effort:* S. *Impact:* M.
   *Files:* repo settings; `tests/test_generate_court_decisions.py:299`; `examples/quickstart.py:84`.

4. **Resolve the re-identification check before anything else touches the court data.**
   *What:* Execute item 1 of `docs/DATA_PROTECTION.md:71-77` — sample the stored Riigikohus names against what `rikos.rik.ee` currently serves and record the result with a date and method. Do the same for CURIA (`:78-80`). If the corpus exceeds the official anonymisation, redact and re-cut the release.
   *Why:* This is the only finding that can convert into a regulator complaint. `curia_combined.jsonld.gz` is already downloadable and `docs/DATA_PROTECTION.md:20` records that party names sit in `rdfs:label` for ~22,290 records. No Estonian public body will co-maintain a corpus whose author has documented an unresolved re-identification risk against live published assets.
   *Effort:* M. *Impact:* H.
   *Files:* `docs/DATA_PROTECTION.md`, potentially `krr_outputs/riigikohus/**`, `krr_outputs/curia/**`, release assets.

5. **Ship the rights and privacy notices inside the release bundle.**
   *What:* Add `NOTICE`, `LICENSE`, `docs/DATA_RIGHTS.md` and `docs/DATA_PROTECTION.md` as v1.0.0+ release assets and to the `SHA256SUMS` manifest; add a short `README.txt` in the bundle pointing at them.
   *Why:* Today someone downloads twelve files containing personal data and third-party legal texts with zero accompanying rights statement. Attribution under Decision 2011/833/EU and the controller warning both travel with the docs, not the graph.
   *Effort:* S. *Impact:* H.
   *Files:* release-asset list in `docs/RELEASE.md`, the release workflow/manual step.

6. **Fix the `CITATION.cff` licence overclaim.**
   *What:* Replace the bare `license: CC-BY-4.0` at `CITATION.cff:21` with a scoped statement — either drop the field and use `license-url:` pointing at `NOTICE`, or keep `CC-BY-4.0` with an explicit `abstract`/`notes` line saying it covers the compilation layer only.
   *Why:* This is the one machine-readable licence assertion in the repo, and it re-asserts exactly the flat claim `docs/DATA_RIGHTS.md:28-32` was written to retract. Zenodo will propagate it into a DOI record the moment #473 is unblocked, making the wrong claim permanent and citable.
   *Effort:* S. *Impact:* H.
   *Files:* `CITATION.cff`.

7. **Make the licence machine-detectable without losing the scope note.**
   *What:* Restore `LICENSE` to bare MIT so GitHub and SPDX scanners detect it, move the SCOPE NOTE to the top of `NOTICE` and to a prominent `README` block, and add `license = "MIT"` plus a `license-files` entry to `pyproject.toml`. Consider a `LICENSES/` directory with `MIT.txt` and `CC-BY-4.0.txt` following the REUSE specification.
   *Why:* A procurement or security team runs a scanner before a human reads anything. `"key":"other"` reads as "unclear licensing" and stalls the file. REUSE compliance is increasingly asked for directly in EU public-sector procurement.
   *Effort:* S. *Impact:* M.
   *Files:* `LICENSE`, `NOTICE`, `README.md`, `pyproject.toml`.

8. **Close the five rights VERIFY items, or state a date by which they will close.**
   *What:* Write to the Riigi Teataja / RIK data owner and the Publications Office for the two questions that need an upstream answer (RT reuse and DB terms; the exact EuroVoc attribution string), and have the data owner elect CC BY 4.0 vs CC0 for layer (b). Record each answer with a date and source in `docs/DATA_RIGHTS.md`.
   *Why:* `docs/DATA_RIGHTS.md:3-7` currently tells every reader not to rely on the document. That instruction, on the rights page of a published dataset, is a hard stop for procurement. Even two of five resolved changes the posture from "unknown" to "partially cleared with an owner".
   *Effort:* M. *Impact:* H.
   *Files:* `docs/DATA_RIGHTS.md`, `NOTICE`.

9. **Write the sustainability story down, then act on the easiest part of it.**
   *What:* Add `GOVERNANCE.md` naming the current maintainer, the decision process, the conditions for adding a maintainer, and an explicit succession/archival plan (e.g. "if unmaintained for 12 months, the corpus and namespace transfer to X, or a final archival release is cut and the repo is marked archived"). State the intended institutional home if there is a candidate — RIK, Justiitsministeerium, Tartu Ülikool, or the Estonian open-data programme. Simultaneously, recruit one legal-domain reviewer and add their handle to the eight safety-critical `CODEOWNERS` lines as `.github/CODEOWNERS:7-9` already anticipates.
   *Why:* Bus factor 1 with a July gap is the finding a funding or procurement committee will lead on. The legal-review gate is the project's headline safety control and it is currently self-review, which is not a control. Naming a successor and adding one reviewer are the two cheapest moves that change the risk profile.
   *Effort:* M. *Impact:* H.
   *Files:* new `GOVERNANCE.md`, `.github/CODEOWNERS`, `README.md`.

10. **Add `SECURITY.md` and a project contact that is not a personal Gmail.**
    *What:* A short disclosure policy (what is in scope, where to report, response-time expectation). Register a project alias (e.g. via the GitHub org or a domain already in hand) and use it in `SECURITY.md`, `w3id/estleg/.htaccess:5-6`, `CITATION.cff`, and as the data-protection contact.
    *Why:* Two distinct needs converge on the same fix: a coordinated-disclosure route for security researchers, and a data-subject contact for GDPR erasure or objection requests (`docs/DATA_PROTECTION.md:84-85`). A personal address also ties the namespace to an individual, which weakens the transfer story in item 9.
    *Effort:* S. *Impact:* M.
    *Files:* new `SECURITY.md`, `w3id/estleg/.htaccess`, `w3id/estleg/README.md`, `CITATION.cff`, `docs/DATA_PROTECTION.md`.

11. **Publish a deprecation and support policy alongside the stability tiers.**
    *What:* Extend `docs/STABILITY.md` with: how long each MINOR remains downloadable, the notice period before a MAJOR (e.g. one MINOR release, or 6 months), whether retired IRIs get `owl:deprecated` + `dcterms:isReplacedBy` rather than disappearing, and what happens to `w3id.org/estleg/<version>` for superseded versions.
    *Why:* `docs/STABILITY.md:18-24` already freezes `@id`s for MINOR/PATCH — good — but a body wiring this into a production service needs to know the support window before it writes SPARQL against `estleg:` IRIs. There is currently no occurrence of "deprecat" in either policy document.
    *Effort:* S. *Impact:* M.
    *Files:* `docs/STABILITY.md`, `docs/RELEASE.md`.

12. **Split the changelog into a consumer-facing release note and the engineering log.**
    *What:* Keep `CHANGELOG.md` as-is for developers. Add a short per-release `docs/RELEASE_NOTES.md` (or a Release body) written for a data consumer: what corpus coverage changed, which properties were added/changed/removed, which heuristic layers were re-run, and what breaks an existing query. Use the Keep a Changelog Added/Changed/Deprecated/Removed groupings there.
    *Why:* A ministry integrator reading `CHANGELOG.md:57-61` ("Citations target lõige when named (#512)") cannot determine whether their queries still work. That determination is the whole purpose of a changelog for an adopter.
    *Effort:* M. *Impact:* M.
    *Files:* `CHANGELOG.md`, new `docs/RELEASE_NOTES.md`, `docs/RELEASE.md`.

13. **Unify the validation-gate command to one source.**
    *What:* Make `CONTRIBUTING.md:30-48` the single definition, correct `.github/PULL_REQUEST_TEMPLATE.md:13` and `CLAUDE.md:23` to match `validate.yml:103` exactly, and add a `make check` / `scripts/check.sh` that CI and contributors both invoke so drift cannot recur.
    *Why:* A reproducible gate is the thing an external contributor or an auditing body runs. Four spellings of the same command is a small defect with a live consequence: `CLAUDE.md` omits `mcp_server/`, which is the directory currently failing lint.
    *Effort:* S. *Impact:* M.
    *Files:* `.github/PULL_REQUEST_TEMPLATE.md`, `CLAUDE.md`, `CONTRIBUTING.md`, new `Makefile` or `scripts/check.sh`.

14. **Retire or clearly archive the stale docs, and move `docs/superpowers/` out of the public tree.**
    *What:* Move `ONTOLOGY_ANALYSIS.md`, `docs/OPEN_ISSUE_VALIDATION_2026-06.md`, `docs/REGULATIONS_INTEGRATION_PLAN.md`, `docs/INTEGRATION_IDEAS.md` into `docs/archive/` with a one-line status header, updating inbound links. Move `docs/superpowers/**` to `docs/archive/design/` (or a private repo), fixing the six inbound references in `README.md:470-471`, `docs/REGULATIONS_INTEGRATION_PLAN.md:12-36`, `docs/NAMESPACE_MIGRATION.md:6,82,140`, `CHANGELOG.md:362`, `tests/test_no_legacy_namespace.py:22` and `validate.yml:257`.
    *Why:* An evaluator cannot tell which document is current. The `superpowers` plans additionally open with instructions addressed to AI agents, which in a repository asserting legal-correctness review reads as unreviewed machine-generated content — an avoidable credibility cost.
    *Effort:* M. *Impact:* M.
    *Files:* the four docs, `docs/superpowers/**`, plus the six referencing files.

15. **Commit `work-overview.html` as a maintained status page, and reconcile its test claims.**
    *What:* Move it to `docs/status.md` (or `docs/work-overview.html`), correct the §3 claims to match CI, and add a README CI badge so health is visible without opening the Actions tab.
    *Why:* The content is the single best adoption artifact in the repo — it explains what shipped, what was deliberately scoped down, and exactly why the four open issues are blocked externally. That transparency is a strength, and it is currently untracked and therefore invisible to anyone who clones. The unreconciled "ruff green / 4033 passed" claims are the one place its credibility is exposed.
    *Effort:* S. *Impact:* M.
    *Files:* `work-overview.html`, `README.md`.

---

## Open questions

1. Was the re-identification check at `docs/DATA_PROTECTION.md:71-77` ever run informally? If the answer is "the names came straight from RIK's anonymised feed and were never joined against another source", that should be written down with the method — it converts the single worst-looking finding into a resolved one at near-zero cost.
2. Is there a candidate institutional home (RIK, Justiitsministeerium, Tartu Ülikool, the Estonian open-data programme), or has any of them been approached? The answer changes whether item 9 is "write a succession plan" or "document a transfer in progress".
3. Was `main` known to be red? Every push since 2026-08-19 failed, which suggests the local gate is trusted over CI. If CI is intentionally treated as advisory, that should be stated — but then it cannot also be cited as the reproducibility story.
4. Is `krr_outputs/kohtud/` intended to grow? `docs/DATA_PROTECTION.md:18` says the committed sample is search-metadata only but a live `--fetch` may copy summaries naming persons. A full lower-court sweep would materially change the GDPR profile and should be gated on the DPO sign-off, not on ingest capability.
5. Who owns the `w3id.org/estleg/` namespace if the maintainer stops? `w3id/estleg/.htaccess:5-8` names one individual. A namespace is a permanent identifier commitment; that is the strongest argument for an institutional home.
6. Does `estleg:containsPersonalData` appear on the *release assets'* in-band dataset heads, or only in `metadata.jsonld`? `docs/DATA_PROTECTION.md:21-22, 89-99` describes the catalog flags; whether a downloaded `curia_combined.jsonld.gz` self-declares is what matters for a reuser who never reads the repo. (Outside my file scope — flagging for whoever holds `metadata.jsonld`.)
