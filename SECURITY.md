# Security policy

This is the private contact route for the Estonian Legal Ontology project. It
covers security vulnerabilities, integrity problems in published data, and
personal data that should not be in the corpus. Other documents, including
`docs/DATA_PROTECTION.md`, point here for anything that must not be reported
in public.

## Supported versions

| Version | Supported |
|---|---|
| Latest 1.x release (currently 1.0.0) | Yes: security fixes, data-integrity fixes and personal-data removals |
| Untagged `main` | Fixed on a best-effort basis; not a supported release |
| Pre-1.0 releases (0.x) | No |

When a new MAJOR line is published, the previous MAJOR line keeps receiving
security fixes for six months (see `GOVERNANCE.md` and `docs/STABILITY.md`).

## How to report

**Do not open a public issue for a vulnerability or for personal data.**

1. **Preferred: GitHub private vulnerability reporting.** On the repository's
   **Security** tab, choose **Report a vulnerability**. The report is visible
   only to you and the maintainer.
   **Pending maintainer decision (#721):** private vulnerability reporting is
   a repository setting that only the maintainer can switch on. Until it is
   enabled, the button is not shown; use step 2.
2. **Fallback while step 1 is unavailable.** Open a public issue titled
   "Security contact request" that contains **no details** of the problem.
   The maintainer will open a draft repository security advisory, add you to
   it, and the report continues there in private.
3. **Project mailbox.** The project has no personal mailbox to publish here.
   **Pending maintainer decision (#721):** a shared, non-personal contact
   alias. When one exists, it will be added to this section and nowhere else,
   so every document that links here picks it up.

Please include what is affected (file path, IRI, release tag or commit), how
to reproduce or observe it, and the impact you expect.

## Response targets

The project has a single maintainer (see `GOVERNANCE.md`), so these targets
are commitments of intent, not a staffed service level.

| Step | Target |
|---|---|
| Acknowledge the report | 5 working days |
| Triage and severity assessment | 10 working days |
| Personal data removed from `main` | 10 working days after confirmation |
| Fix for a critical or high issue | 30 days |
| Fix for a medium or low issue | Next release |

Fixes are disclosed through a GitHub Security Advisory and the release notes
once a fixed release is available. Reporters are credited unless they ask not
to be.

## Scope

**In scope: code.**

- The MCP server in `mcp_server/`, in particular its streamable-HTTP
  transport, which is exposed to remote clients.
- The ingest and build code in `src/estleg/`, `scripts/` and
  `estleg_client/`: XML and HTML parsing of fetched documents, path handling,
  and anything that runs on data from an untrusted source.
- The CI workflows in `.github/workflows/`.

**In scope: data integrity.**

- A published release asset whose content does not match its `SHA256SUMS`
  entry or the `spdx:checksum` in `metadata.jsonld`.
- Identifiers under `https://w3id.org/estleg/` that resolve somewhere other
  than this project's releases.
- Deliberately or systematically falsified legal content, such as a sanction
  amount or a court holding that differs from its cited source in a way that
  points to tampering rather than to an extraction error.

**In scope: personal data.**

- An Estonian personal identification code (isikukood), or other identifying
  detail, in the court-decision corpora that should have been masked. See
  `docs/DATA_PROTECTION.md` for what the project screens and how.

**Out of scope here; use a public issue instead.**

- Ordinary extraction or classification errors in the legal data. Use the
  data-correction issue template, which routes the issue to legal review.
- Validation-gate failures already listed in `docs/VALIDATION_REPORT.md`.
- Vulnerabilities in third-party dependencies with no demonstrated impact on
  this project. Dependabot tracks those.
- The upstream sources (Riigi Teataja, RIK, EUR-Lex, CURIA). Report those to
  their operators.
