# Governance

This document says who decides what in the Estonian Legal Ontology project,
how a release is supported, how legal-correctness review is routed, and what
happens to the project and its identifiers if the maintainer becomes
unavailable. It is the governance record asked for in #721.

Items marked **Pending maintainer decision** are deliberately left open: they
depend on a choice only the maintainer can make (a person to appoint, a repo
setting, an institutional agreement). Each one names the issue that tracks it.

## Status in one paragraph

The project is maintained by one person. On 2026-10-08, at commit
`c0140d497e`, every human-authored commit in the history (390 of 390) is by
the maintainer; the only other author is Dependabot (9 commits). The bus
factor is therefore **1**. The legal-correctness review that
`CONTRIBUTING.md` requires for safety-critical data is today routed back to
the same person, so it is self-review. The mitigations below reduce the cost
of that situation; they do not remove it. Removing it needs a second
maintainer, a legal-domain reviewer and an institutional home, all of which
are pending.

## Roles

| Role | Who | Responsibilities |
|---|---|---|
| Maintainer | `@henrikaavik` | Merges to `main`, cuts releases, owns the namespace (`https://w3id.org/estleg/`), triages issues and security reports, decides on everything this document does not delegate. |
| Legal-domain reviewer | **Pending maintainer decision (#721)** | Signs off changes to the safety-critical paths listed under [Legal-review routing](#legal-review-routing). Must be someone other than the author of the change and able to judge Estonian or EU law on the merits. |
| Contributors | Anyone opening an issue or pull request | Follow `CONTRIBUTING.md` and `AGENTS.md`; run the validation gates; flag safety-critical changes in the pull-request template. |
| Automated contributors | Dependabot; AI coding agents run by the maintainer | Their changes are proposals. They are reviewed and merged by the maintainer like any other contribution, and agent-written commits carry a `Co-Authored-By` trailer. |

A contributor becomes a maintainer by invitation from the existing maintainer
after a sustained record of reviewed contributions. The invitation is recorded
in this file and in `.github/CODEOWNERS`.

## How decisions are made

- **Routine changes** (bug fixes, data corrections with a cited source,
  documentation) are decided by pull-request review. The maintainer merges
  when the required checks are green and review comments are resolved.
- **Consumer-contract changes** (anything `docs/STABILITY.md` classes as
  MINOR or MAJOR: a new or removed property, an `@id` scheme change, a change
  to a predicate tier) need an issue first, stating the consumer impact. The
  change ships with a `docs/RELEASE_NOTES.md` entry and follows the
  deprecation window in `docs/STABILITY.md`.
- **Legal-correctness changes** need the sign-off described below. When the
  correct value cannot be sourced authoritatively, the default is to remove
  the assertion or mark it low-confidence, not to substitute a plausible one.
- **Rights and personal-data questions** (`NOTICE`, `docs/DATA_RIGHTS.md`,
  `docs/DATA_PROTECTION.md`) are decided by the maintainer as data owner.
  Open legal verification items stay visibly open until evidence closes them.
- Disagreements are discussed on the issue or pull request. The maintainer
  takes the final decision and records the reason there.

## Legal-review routing

`.github/CODEOWNERS` lists the safety-critical paths under the
"needs-legal-review" heading: 14 path lines in five areas (sanctions, deontic
classification, Riigikohus and CURIA court decisions, EU-directive
transposition and harmonisation, institutional competence). GitHub requests a
review from every owner on those lines when a pull request touches them.
`tests/test_codeowners.py` keeps the list pointing at the real implementation
modules and in sync with `CONTRIBUTING.md`.

The routing works like this:

1. The author ticks the safety-critical box in the pull-request template and
   adds the `needs-legal-review` label. Data-correction issues get the label
   from their issue template.
2. GitHub requests review from the CODEOWNERS of the touched paths.
3. The pull request is not merged until a legal-domain reviewer has confirmed
   the asserted facts.

**Pending maintainer decision (#721):** today every one of those lines names
only `@henrikaavik`, so step 3 is self-review. Appointing a legal-domain
reviewer means adding their GitHub handle next to `@henrikaavik` on each
needs-legal-review line. Making the review binding means enabling branch
protection on `main` with "Require review from Code Owners". Branch
protection is a repository setting, not a file in this repository.

## Releases and support

- **Versioning.** Semantic versioning, applied to the consumer contract in
  `docs/STABILITY.md`. The rules for MAJOR, MINOR and PATCH and the release
  procedure are in `docs/RELEASE.md`.
- **Cadence.** A MINOR or PATCH release follows each corpus refresh from
  Riigi Teataja or a batch of consumer-visible fixes. The corpus refresh
  target is monthly, matching the refresh SLA and the per-corpus lag budgets
  in `docs/RELEASE.md`. There is no fixed calendar; a release is cut when the
  release gates pass.
- **What a consumer reads.** `docs/RELEASE_NOTES.md` is the consumer-facing
  summary of each release. `CHANGELOG.md` is the engineering log.
- **Support.** The latest MINOR release of the current MAJOR line receives
  data corrections and fixes. Security fixes are also made for the previous
  MAJOR line for six months after a new MAJOR is published. Untagged `main`
  is not a supported release. The support horizon and the deprecation window
  are stated as a contract in `docs/STABILITY.md`; `SECURITY.md` lists the
  supported versions.

## Succession

If the maintainer is unavailable, the project must stay usable and its
identifiers must keep resolving. The mechanism is:

1. **Published artefacts survive on their own.** Tagged GitHub Releases carry
   the data, the `SHA256SUMS` file and the rights notices, so a consumer who
   pinned a release keeps working without the maintainer.
2. **Namespace.** `https://w3id.org/estleg/` is a w3id.org PURL whose
   redirect rules live in the public `perma-id/w3id.org` repository. The
   w3id.org community can repoint them to a successor repository through a
   pull request, so the identifiers do not depend on this repository staying
   where it is.
3. **Repository.** The fallback is a transfer of the GitHub repository to a
   named organisation that has agreed to steward it, or, failing that,
   archiving the repository read-only with a pointer to the deposit below.
   **Pending maintainer decision (#730):** which institution provides the
   institutional home and accepts the transfer. Until #730 is decided there
   is no named successor, and a long absence of the maintainer would leave
   the repository frozen but readable.
4. **Hosted services.** The MCP endpoint and the Seadusloome backend run on
   infrastructure the maintainer operates. They are not covered by this
   succession plan and would stop with the maintainer; the data and code
   needed to rebuild them are in the repository.

## Archival

- **Pending maintainer decision (#473):** a Zenodo deposit with a DOI for
  each release. It needs a Zenodo account token that only the maintainer can
  create.
- **Pending maintainer decision (#727):** a deposit with CLARIN-EE, the
  Estonian node of the CLARIN research infrastructure, as part of the
  benchmark packaging work.
- Until one of these lands, the GitHub Releases are the only durable copy.
  They are not an archive: they depend on the repository and the account
  that owns it.

## What mitigates the bus factor today

- The working conventions are written down (`AGENTS.md`, `CONTRIBUTING.md`,
  `docs/RELEASE.md`), and the release DAG and all validation gates are
  scripted and run in CI. Another engineer can rebuild and validate a release
  from the repository alone.
- `constraints.txt` pins the build toolchain, so a rebuild on another machine
  uses the same library versions.
- The identifiers are under w3id.org, not under a domain the maintainer must
  keep paying for.
- Releases ship their rights and personal-data notices with the data.

## Contact

There is no personal mailbox for the project. Security, personal-data and
other private reports go through the route in [SECURITY.md](SECURITY.md).
Everything else goes to the GitHub issue tracker.
