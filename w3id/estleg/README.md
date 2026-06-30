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

A content-negotiating **303 resolver** (RDF vs HTML per `Accept`) is planned;
the interim `.htaccess` redirects to the project repository.

---

## How this directory is registered (maintainer action)

This `estleg/` directory (`.htaccess` + this `README.md`) is staged in the
ontology repo under `w3id/estleg/`. To activate the namespace, **the maintainer
submits a pull request** copying it to
[`perma-id/w3id.org`](https://github.com/perma-id/w3id.org) at path `estleg/`,
per that repo's contribution guide. Until that PR is merged,
`https://w3id.org/estleg/` returns 404.
