# One definition of the project checks, shared by CI and contributors (#721).
#
#   make check          lint + default test tier + docs lint (the fast CI jobs)
#   make lint           ruff over the corpus tooling and the MCP server
#   make test           default pytest tier (no Git LFS needed)
#   make docs-lint      markdownlint with the repo's .markdownlint.json
#   make corpus-gates   full-corpus validators; NEEDS `git lfs pull` first
#   make release-assets build release/ (see docs/RELEASE.md#release-assets)
#
# Override the interpreter with `make PYTHON=.venv/bin/python check`.
# Extra pytest flags go in PYTEST_ARGS, e.g. `make test PYTEST_ARGS="-n auto"`.

PYTHON ?= python3
MARKDOWNLINT ?= markdownlint
PYTEST_ARGS ?=

# The canonical lint paths. CI's `lint` job calls `make lint`, and
# CONTRIBUTING.md, AGENTS.md, CLAUDE.md and the PR template quote this
# command verbatim. Change it here and in those four files together.
LINT_PATHS := scripts/ src/estleg/ tests/ mcp_server/

.PHONY: help check lint test docs-lint corpus-gates release-assets

help:
	@sed -n '3,11p' Makefile

check: lint test docs-lint

lint:
	$(PYTHON) -m ruff check $(LINT_PATHS)

test:
	$(PYTHON) -m pytest -q $(PYTEST_ARGS)

# Same globs as the CI `Docs lint` job. Needs markdownlint-cli on PATH
# (CI installs markdownlint-cli@0.41.0 with npm).
docs-lint:
	$(MARKDOWNLINT) 'docs/*.md' 'docs/proposals/*.md' 'README.md'

# Requires the Git LFS artifacts (combined_ontology.jsonld and the other
# LFS-tracked aggregates): run `git lfs pull` first. On a pointer-only
# checkout these validators measure a smaller corpus and the result is not
# meaningful. Takes tens of minutes; the SHACL buckets dominate.
corpus-gates:
	$(PYTHON) scripts/validate_all.py
	$(PYTHON) scripts/shacl_validate_all.py --all
	$(PYTHON) scripts/check_phantom_typing.py --all

# Writes the downloadable release files into release/ (gitignored). Needs
# Git LFS artifacts. Uploading them to a GitHub Release stays manual.
release-assets:
	$(PYTHON) scripts/build_release_assets.py
