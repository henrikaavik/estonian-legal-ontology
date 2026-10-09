# `https://w3id.org/estleg/` — Estonian Legal Ontology

Persistent namespace for the **Estonian Legal Ontology** (`estleg`): a
machine-readable JSON-LD/RDF ontology of Estonian and EU law.

- **Namespace:** `https://w3id.org/estleg/`
- **Term IRI shape:** `https://w3id.org/estleg/<LocalName>` (e.g.
  `https://w3id.org/estleg/KARIST_2_Osa2_Par_141`, karistusseadustik § 141)
- **Ontology / version IRI:** `https://w3id.org/estleg`, `https://w3id.org/estleg/<version>`
- **Vocabulary (T-Box):** `https://w3id.org/estleg/vocabulary`
- **Project / source:** <https://github.com/henrikaavik/estonian-legal-ontology>
- **Maintainer / contact:** Henrik Aavik — <https://github.com/henrikaavik>
- **Adopted:** issue #516 — replaces the non-resolvable, government-owned
  `data.riik.ee/ontology/estleg#` scheme before the v1.0.0 freeze.

---

## Status

**Registered.** [`perma-id/w3id.org` PR #6575][pr] was merged on
**2026-08-19**. What is live today:

| IRI | Live result |
|---|---|
| `https://w3id.org/estleg/` | `302` to the project repository |
| `https://w3id.org/estleg/1.0.0` | `302` to `releases/tag/v1.0.0` |
| any other IRI, any `Accept` | `302` to the project repository |

**Staged, not live (#728).** The `.htaccess` in this directory adds content
negotiation. It takes effect only after the pull request below is merged, and
that PR must not be opened before the resolver host answers (see
[Prerequisites](#prerequisites-before-opening-the-pr)).

| IRI | `Accept` | Staged result |
|---|---|---|
| `/estleg/` | any | `302` repository (unchanged) |
| `/estleg/<MAJOR.MINOR.PATCH>` | RDF (`text/turtle`, `application/ld+json`, `application/n-triples`, `application/rdf+xml`) | `303` `controlled_vocabulary.jsonld` as committed at tag `v<version>` (raw blob; release assets are `application/octet-stream`, which rdflib refuses) |
| `/estleg/<MAJOR.MINOR.PATCH>` | anything else | `302` `releases/tag/v<version>` (unchanged, #690) |
| `/estleg/dataset/…`, `/estleg/graph/…` | RDF / other | `303` raw `void.ttl` / its GitHub page |
| `/estleg/vocabulary` | any | `303` `https://estleg.sixtyfour.ee/vocabulary` |
| `/estleg/<local>` | RDF, `application/json`, `text/html`, `application/xhtml+xml` | `303` `https://estleg.sixtyfour.ee/id/<local>` |
| `/estleg/<local>` | `*/*` only, or other types | `302` repository (interim, unchanged) |
| `/estleg/a/b` (multi-segment) | any | `302` repository |

Every response carries `Vary: Accept`. The resolver host appears **once**, in
the `SetEnvIf … RESOLVER=` line at the top of `.htaccess`; moving the resolver
is a one-line change plus a re-submission. `tests/test_w3id_htaccess.py`
parses the staged file and asserts each row of this table.

### The 303 flow

```text
client                       w3id.org                         resolver host
  | GET /estleg/KARIST_2_Osa2_Par_141                             |
  |   Accept: text/turtle  ->  303 See Other                      |
  |                            Location: https://estleg.sixtyfour.ee/id/KARIST_2_Osa2_Par_141
  | GET /id/KARIST_2_Osa2_Par_141   Accept: text/turtle  ------->  |
  |   <- 200 text/turtle, Vary: Accept, ETag, Cache-Control         |
```

w3id.org does not proxy. The client follows the `303` and sends its own
`Accept` again, so the resolver negotiates exactly what the client asked for.
The resolver (`estleg-mcp`, `/id/{local}` and `/vocabulary`) is documented in
[`mcp_server/README.md`](../../mcp_server/README.md#w3id-resolver-728); the
operating model and open decisions are in
[`docs/proposals/2026-10-w3id-content-negotiation.md`](../../docs/proposals/2026-10-w3id-content-negotiation.md).

---

## How to update the PURL

This directory (`.htaccess` + this `README.md`) is a **staging copy**. The
live rules are whatever `perma-id/w3id.org` holds at `estleg/`. Any change
here must be re-submitted as a pull request to `perma-id/w3id.org` from a
GitHub account listed as maintainer in the file (`henrikaavik`). Nobody else
can open it.

### Prerequisites before opening the PR

1. An estleg-mcp build containing the resolver is deployed at the host named
   in `RESOLVER=` with `ESTLEG_RESOLVER` unset (on), and rdflib is installed
   in the image (the `http` extra does not include it yet).
2. These answer without a token:

   ```bash
   curl -sI https://estleg.sixtyfour.ee/id/KARIST_2_Map | head -1          # HTTP/2 200
   curl -s  -H 'Accept: text/turtle' https://estleg.sixtyfour.ee/id/KARIST_2_Map | head -3
   curl -s  -o /dev/null -w '%{http_code}\n' https://estleg.sixtyfour.ee/id/No_Such   # 404
   curl -s  -o /dev/null -w '%{http_code}\n' -X POST https://estleg.sixtyfour.ee/mcp # 401
   ```
3. The operator decision in the ADR is taken. Pointing a persistent namespace
   at a host is a promise to keep that host answering.

### The pull request (exact text)

Branch in a fork of `perma-id/w3id.org`, copy both files over `estleg/`,
commit, and open the PR:

```bash
cp w3id/estleg/.htaccess w3id/estleg/README.md ../w3id.org/estleg/
cd ../w3id.org && git checkout -b estleg-conneg
git add estleg/.htaccess estleg/README.md
git commit -m "estleg: content-negotiating 303 rules"
gh pr create --repo perma-id/w3id.org --title "estleg: content-negotiating 303 rules" --body-file - <<'EOF'
Updates the existing `estleg` namespace (registered in #6575). I am the
maintainer listed in `estleg/.htaccess` (GitHub `henrikaavik`).

- Term IRIs (`/estleg/<local>`) with an RDF or HTML `Accept` now 303 to the
  project's per-node resolver at https://estleg.sixtyfour.ee/id/<local>, which
  serves JSON-LD, Turtle, N-Triples, RDF/XML or an HTML description page.
- `/estleg/vocabulary` 303s to the served vocabulary.
- Version IRIs (`/estleg/<MAJOR.MINOR.PATCH>`) generalise the hard-coded
  `1.0.0` rule; RDF clients get the T-Box at that tag, browsers the release.
- `/estleg/dataset/…` and `/estleg/graph/…` 303 to the VoID description.
- Everything else keeps the existing 302 to the project repository.
- `Header always set Vary "Accept"`; the resolver host is a single
  `SetEnvIf` variable.

The resolver host already answers these routes anonymously; the rule set is
unit-tested in the source repository (tests/test_w3id_htaccess.py).

- [x] Update of an existing ID
- [x] I am a maintainer listed in the `.htaccess`
EOF
```

### The diff (operative lines)

Against the staging copy at commit `b974a75810` (the comments are rewritten
too and are not shown). If the live file still has the hard-coded `1.0.0`
version rule from #6575, the PR replaces that line the same way.

```diff
-Options +FollowSymLinks
+Options +FollowSymLinks -MultiViews
-RewriteRule ^(\d+\.\d+\.\d+)/?$ https://github.com/henrikaavik/estonian-legal-ontology/releases/tag/v$1 [R=302,L]
+SetEnvIf Request_URI ^.*$ RESOLVER=https://estleg.sixtyfour.ee
+SetEnvIf Request_URI ^.*$ REPO=https://github.com/henrikaavik/estonian-legal-ontology
+SetEnvIf Request_URI ^.*$ RAW=https://raw.githubusercontent.com/henrikaavik/estonian-legal-ontology
+Header always set Vary "Accept"
+RewriteRule ^$ %{ENV:REPO} [R=302,L]
+RewriteCond %{HTTP_ACCEPT} (application/(ld\+json|n-triples|rdf\+xml)|text/turtle) [NC]
+RewriteRule ^(\d+\.\d+\.\d+)/?$ %{ENV:RAW}/v$1/krr_outputs/controlled_vocabulary.jsonld [R=303,L]
+RewriteRule ^(\d+\.\d+\.\d+)/?$ %{ENV:REPO}/releases/tag/v$1 [R=302,L]
+RewriteCond %{HTTP_ACCEPT} (application/(ld\+json|n-triples|rdf\+xml)|text/turtle) [NC]
+RewriteRule ^(dataset|graph)(/.*)?$ %{ENV:RAW}/main/krr_outputs/void.ttl [R=303,L]
+RewriteRule ^(dataset|graph)(/.*)?$ %{ENV:REPO}/blob/main/krr_outputs/void.ttl [R=303,L]
+RewriteRule ^vocabulary/?$ %{ENV:RESOLVER}/vocabulary [R=303,L]
+RewriteCond %{HTTP_ACCEPT} (application/(ld\+json|json|n-triples|rdf\+xml|xhtml\+xml)|text/(turtle|html)) [NC]
+RewriteRule ^([^/]+)$ %{ENV:RESOLVER}/id/$1 [R=303,L]
-RewriteRule ^(.*)$ https://github.com/henrikaavik/estonian-legal-ontology [R=302,L]
+RewriteRule ^(.*)$ %{ENV:REPO} [R=302,L]
```

### After the merge

```bash
curl -sI -H 'Accept: text/turtle' https://w3id.org/estleg/KARIST_2_Osa2_Par_141 | grep -iE '^(HTTP|location|vary)'
#  HTTP/2 303 · location: https://estleg.sixtyfour.ee/id/KARIST_2_Osa2_Par_141 · vary: Accept
python3 -c "import rdflib; print(len(rdflib.Graph().parse('https://w3id.org/estleg/KARIST_2_Osa2_Par_141', format='turtle')))"
curl -sI https://w3id.org/estleg/ | head -1                      # still 302 to the repository
```

**Rollback.** Revert the PR on `perma-id/w3id.org`, or point `RESOLVER=` at a
host that answers. Turning the resolver off on the server (`ESTLEG_RESOLVER=off`)
does **not** roll back: w3id keeps 303-ing to a host that then answers `401`.

[pr]: https://github.com/perma-id/w3id.org/pull/6575
