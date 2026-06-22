<!-- Thanks for contributing. Fill this in; delete the guidance comments. -->

## What & why

<!-- What does this change and why. Link the issue: "Fixes #NNN" or "Refs #NNN". -->

Fixes #

## Validation gates

<!-- All must pass locally before review. CI runs the same set. -->

- [ ] `python3 -m ruff check scripts/ tests/ mcp_server/`
- [ ] `python3 -m pytest -q`
- [ ] `python3 scripts/validate_all.py`
- [ ] `python3 scripts/shacl_validate_all.py --all`
- [ ] `python3 scripts/validate_seadusloome_sync.py`
- [ ] If `combined_ontology.jsonld` was regenerated: `python3 -m pytest -q -m corpus`

## Generated artifacts

- [ ] I did **not** hand-edit generated artifacts (changed the generator + regenerated), **or** used a documented surgical post-processor and explained it below.

## Legal-correctness review

<!-- See CONTRIBUTING.md → "Mandatory legal-correctness review". -->

- [ ] This PR changes **safety-critical legal data** (sanctions, deontic
      `normativeType`, court decisions, transposition/harmonisation, or
      institutional competence).
  - If checked: label the PR `needs-legal-review`, and the asserted facts have
    been confirmed by a legal-domain reviewer (or the wrong assertion was
    removed / marked low-confidence rather than guessed).
- [ ] This PR does **not** touch safety-critical legal data.

## Notes

<!-- Anything reviewers should know: surgical post-processing, deferred work, follow-ups. -->
