# EuroVoc overlay (issue #463 pilot)

Heuristic EuroVoc subjects used to be written into `*_peep.json` act
nodes. A `generate_all_laws` regen then wiped them and forced a full
enrichment re-run.

## Contract

- **Peeps** keep scraped facts (`legalText`, structure, `rdfs:label`).
- **`krr_outputs/eurovoc/eurovoc_overlay.jsonld`** holds
  `dcterms:subject` / `eli:is_about` on the same act IRIs.
- **`generate_combined_jsonld`** merges `eurovoc/` (see
  `COMBINED_OVERLAY_SUBDIRS`) so combined still answers subject queries.

`classify_eurovoc` writes the overlay by default. `--write-peeps` is the
legacy in-place path. Regenerating a law peep does not delete the overlay.

Other heuristic layers (deontic, targetGroup, similarity) stay in peeps
until they get the same treatment.
