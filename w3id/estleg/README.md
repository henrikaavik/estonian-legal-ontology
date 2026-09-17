# `https://w3id.org/estleg/` — Estonian Legal Ontology

Persistent namespace for the **Estonian Legal Ontology** (`estleg`): a
machine-readable JSON-LD/RDF ontology of Estonian and EU law.

- **Namespace:** `https://w3id.org/estleg/`
- **Term IRI shape:** `https://w3id.org/estleg/<LocalName>` (e.g.
  `https://w3id.org/estleg/KarS_Par_141`)
- **Ontology / version IRI:** `https://w3id.org/estleg`, `https://w3id.org/estleg/<version>`
- **Project / source:** <https://github.com/henrikaavik/estonian-legal-ontology>
- **Maintainer / contact:** Henrik Aavik — <https://github.com/henrikaavik>
- **Adopted:** issue #516 — replaces the non-resolvable, government-owned
  `data.riik.ee/ontology/estleg#` scheme before the v1.0.0 freeze.

The namespace is **live**. A content-negotiating **303 resolver** (RDF vs HTML
per `Accept`) is still planned and tracked as **#728**; the interim `.htaccess`
302-redirects to the project repository.

---

## How this directory is registered (maintainer action)

This `estleg/` directory (`.htaccess` + this `README.md`) is staged in the
ontology repo under `w3id/estleg/`. To activate the namespace, **the maintainer
submits a pull request** copying it to
[`perma-id/w3id.org`](https://github.com/perma-id/w3id.org) at path `estleg/`,
per that repo's contribution guide.

**Status: registered.** [`perma-id/w3id.org` PR #6575][pr] was merged on
**2026-08-19**, and the PURL resolves:

| IRI | Result |
|---|---|
| `https://w3id.org/estleg/` | `302` to the project repository |
| `https://w3id.org/estleg/1.0.0` | `302` to `releases/tag/v1.0.0` |
| `https://w3id.org/estleg/<any other version>` | `302` to the project repository |

Content negotiation is **not** live (tracked as **#728**), so the commented
`303` block in `.htaccess` must stay commented.

> **This directory is a staging copy, not the served one.** The live rules are
> whatever `perma-id/w3id.org` holds at `estleg/`. Any change made here — the
> SemVer version rule included — **must be re-submitted as a new pull request to
> `perma-id/w3id.org`** before it takes effect on `https://w3id.org/estleg/`.

[pr]: https://github.com/perma-id/w3id.org/pull/6575
