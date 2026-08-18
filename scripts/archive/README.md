# Spent one-shot scripts

These migrations already ran against the corpus. Do **not** run them on the
live tree.

- `migrate_multipart_iri_scheme.py` — multipart `_OsaN` IRI rewrite (spent).
- `fix_duplicate_ids.py` — cross-file `@id` deduplicator (spent).

`migrate_uris.py` stays in `scripts/`. It still owns the URI registry and
must not be archived with this directory.
