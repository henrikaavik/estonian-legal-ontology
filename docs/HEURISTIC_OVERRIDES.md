# Heuristic overrides

Issue #700. The keyword classifiers clear their own layer before they rewrite
it, so a correction typed straight into a peep is lost on the next
regeneration. A reviewed correction goes into the git-tracked override store
instead: [`data/heuristic_overrides.jsonl`](../data/heuristic_overrides.jsonl).
Every classifier reads it on every run, so the correction survives
regeneration by construction.

## What can be overridden

| Classifier | Predicate | Value form (the peep's own form) |
|---|---|---|
| `classify_deontic.py` | `estleg:normativeType` | one `{"@id": "estleg:NormType_<Obligation\|Right\|Permission\|Prohibition\|Definition>"}` |
| `classify_deontic.py` | `estleg:dutyHolder` | non-empty list of `{"@id": "estleg:TargetGroup_<Citizen\|Business\|PublicBody\|Official\|NGO>"}` |
| `classify_target_group.py` | `estleg:targetGroup` | non-empty list of `{"@id": "estleg:TargetGroup_*"}` (same five) |
| `extract_institutional_competence.py` | `estleg:competentAuthority` | non-empty list of `{"@id": "estleg:..."}` |
| `extract_institutional_competence.py` | `estleg:competenceType` | one of `supervision`, `licensing`, `enforcement`, `regulation`, `advisory`, `general` |
| `classify_eurovoc.py` | `dcterms:subject` | non-empty list of `{"@id": "http://eurovoc.europa.eu/<digits>"}` |

For `dcterms:subject` the override owns only the EuroVoc part. Non-EuroVoc
subjects on the node are kept, and `eli:is_about` mirrors the EuroVoc refs.
Any other predicate is rejected on load.

## Record format

One JSON object per line. Blank lines are ignored. JSONL has no comment
syntax, so put the reasoning in `basis`.

```json
{"node": "estleg:KarS_Par_121_Lg1", "predicate": "estleg:normativeType", "value": {"@id": "estleg:NormType_Prohibition"}, "reviewer": "Justiitsministeerium", "date": "2026-10-01", "basis": "RT I, 10.03.2023, 12; KarS § 121 lg 1 is a penal prohibition", "action": "set"}
{"node": "estleg:KarS_Par_121_Lg1", "predicate": "estleg:dutyHolder", "reviewer": "Justiitsministeerium", "date": "2026-10-01", "basis": "no addressee; penal norm", "action": "remove"}
```

| Key | Rule |
|---|---|
| `node` | Compact `estleg:` IRI of the node, exactly as it appears in the peep (or, for EuroVoc, the act IRI). |
| `predicate` | One of the six predicates above. |
| `value` | Required for `set`, in the form above. Omitted or `null` for `remove`. |
| `reviewer` | A role or organisation, never a person's name or address. It is published as `prov:wasAttributedTo`. |
| `date` | Review date, `YYYY-MM-DD`. |
| `basis` | The Riigi Teataja citation or a short reason. |
| `action` | `set` makes the node carry exactly `value`. `remove` makes it carry no value for the predicate. |

The loader fails hard, naming the line, on invalid JSON, unknown or missing
keys, an unknown predicate, a malformed node IRI, a value in the wrong form,
a bad date, an e-mail-like reviewer, or a second line for the same
(`node`, `predicate`) pair. A classifier with a broken store exits non-zero
before it touches any file. An explicitly supplied `--overrides` path must
exist; use an empty file when intentionally running without overrides.

## Workflow

1. **Who adds a line.** Anyone opening a pull request, on behalf of the
   reviewing body named in `reviewer`. A ministry or agency reviewer may
   send the correction as an issue, and a maintainer then turns it into a line.
2. **Review basis.** The pull-request reviewer checks the `basis` against the
   cited provision text. A correction without a citable basis is not merged.
3. **Validate.** Run the store check before committing:

   ```bash
   python3 scripts/heuristic_overrides.py validate
   python3 scripts/heuristic_overrides.py list --classifier deontic
   ```

4. **Regenerate.** Run the affected classifier as usual. Every classifier:
   - skips owned (`node`, `predicate`) pairs in its "clear before rewrite" step;
   - computes the heuristic result, then applies the override **last**, so the
     override always wins;
   - writes `prov:wasAttributedTo` on the node as a plain string literal naming
     the reviewer, or a sorted list when several reviewers own predicates on
     that node;
   - restamps `estleg:assertionConfidence`, where an owned layer counts as 1.0;
   - adds the `prov` prefix to the peep's `@context`.
5. **Retract.** Delete the line and rerun the classifier. The heuristic value
   returns, and `prov:wasAttributedTo` and the human 1.0 confidence are removed
   from the node. `prov:wasAttributedTo` on peep and overlay nodes is owned by
   this mechanism, so it is safe to remove.

### Confidence semantics

`estleg:assertionConfidence` is node-level. It is the minimum over the
heuristic layers present on the node (`normativeType` 0.70, `targetGroup` 0.65,
EuroVoc `dcterms:subject` 0.55). A layer owned by an override scores 1.0. A
node whose layers are all human-owned, or that carries only removals or
non-layer overrides, reads 1.0. A node with a reviewed `normativeType` and a
still-heuristic `targetGroup` therefore reads 0.65.

### Where each classifier applies overrides

- **Deontic.** It applies to any node in a peep `@graph`, including nodes with
  no classifiable text.
- **Target group.** It applies to provision nodes and to any other node the
  store names. The `--stamp-confidence` pass is override-aware.
- **Competence.** A reviewed `competentAuthority` replaces detection for that
  provision. `estleg:Institution_*` refs get institution back-links
  (`appliesToProvision`) when the institution is canonical and its label is
  known. Other refs are counted as skipped. A reviewed `competenceType` is
  used for the provision and its back-links.
- **EuroVoc.** In the default overlay mode, an owned act gets an overlay node
  with the reviewed subjects, or none for `remove`, plus attribution and
  confidence. Under legacy `--write-peeps`, the peep's EuroVoc subjects are
  never cleared for an owned act, and the reviewed set is written instead.

## Listing stale entries

An override whose node no longer exists is stale. This happens after a
renumbering or an `@id` migration. Stale lines are harmless but should be
re-pointed or deleted.

```bash
python3 scripts/heuristic_overrides.py stale            # all classifiers; exit 1 if any
python3 scripts/heuristic_overrides.py stale --classifier eurovoc
python3 scripts/classify_deontic.py --check-overrides    # dry run, writes nothing
python3 scripts/classify_target_group.py --check-overrides
python3 scripts/extract_institutional_competence.py --check-overrides
python3 scripts/classify_eurovoc.py --check-overrides
```

Each `--check-overrides` run reports how many of that classifier's overrides
would apply and lists the stale ones by line number.
