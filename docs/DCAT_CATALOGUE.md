# DCAT-AP catalogue record

`metadata.jsonld` at the repository root is the dataset's catalogue record. It
targets **DCAT-AP 3.0.1** and the Estonian **Andmekirjelduse standard 3.0.1**,
so that andmed.eesti.ee can harvest it and forward it to data.europa.eu (#710).
The rights model it encodes is explained in [`DATA_RIGHTS.md`](DATA_RIGHTS.md).

## Shape

The JSON root is still the `dcat:Dataset`, so existing consumers can keep
reading keys such as `owl:versionInfo`, `dcat:distribution` and
`estleg:statistics` at the top level. The catalogue is attached with JSON-LD
`@reverse`. In RDF the record therefore states that the catalogue
`dcat:dataset` the dataset.

| Node | IRI | Notes |
| --- | --- | --- |
| Catalogue | `https://w3id.org/estleg/catalog` | Title and description in et and en, publisher, homepage, languages, `conformsTo` DCAT-AP 3.0.1 and the Estonian profile |
| Dataset | `https://w3id.org/estleg/dataset/estonian-legal-ontology` | No `dcterms:license`; `accessRights` public; EU theme, country and frequency IRIs |
| Publisher | `https://w3id.org/estleg/publisher` | `foaf:Organization` for the project; the maintainer is `dcterms:creator` |
| Contact point | blank node | `vcard:Kind` with `vcard:fn`, `vcard:hasEmail` and `vcard:hasURL` (the issue tracker) |
| Distributions | `…/estonian-legal-ontology#dist-<key>` | One per download surface, listed below |

The Estonian profile publishes no IRI of its own. The record names it in a
`dcterms:Standard` node that links the published guideline.

## Distributions and licences

Every distribution carries `dcterms:license`, `dcterms:accessRights`,
`dcterms:identifier`, `dcterms:format` from the EU file-type authority,
`dcterms:accrualPeriodicity` from the EU frequency authority, and a
`dcterms:RightsStatement`. RDF distributions also carry `dcterms:conformsTo`
for the SHACL shapes and the controlled vocabulary.

| Distribution | Licence | Frequency |
| --- | --- | --- |
| JSON-LD ontology files (complete dataset) | layered-terms document `#licence-layered` | MONTHLY |
| Combined enacted laws ontology | CC BY 4.0 (compilation layer) | MONTHLY |
| Domestic regulations (state-level) | CC BY 4.0 (compilation layer) | MONTHLY |
| Combined draft legislation ontology | CC BY 4.0 (compilation layer) | MONTHLY |
| Combined EU legislation ontology | COM_REUSE (Decision 2011/833/EU) | QUARTERLY |
| Combined EU court decisions ontology | COM_REUSE (Decision 2011/833/EU) | QUARTERLY |
| Combined Supreme Court decisions ontology (Riigikohus) | CC BY 4.0 (compilation layer) | QUARTERLY |
| Municipal regulations ontology (KOV) | CC BY 4.0 (compilation layer) | MONTHLY |
| Inter-release change record 0.11.0 | CC BY 4.0 (wholly project-authored) | IRREG |
| Retrieval chunks (provision-version JSON Lines) | CC BY 4.0 (compilation layer) | MONTHLY |

Licence IRIs come from the EU licence authority
(`http://publications.europa.eu/resource/authority/licence/`). Where the
licence is CC BY 4.0, it covers only what the project can license: the
compilation layer. The rights statement on each distribution says so. These
elections are DRAFT and await sign-off, as recorded in `NOTICE`.

## URL pinning

Release downloads use `releases/download/v<version>/`. Tree, blob and raw
links, and the source archive, use the same tag (`tree/v<version>/…`,
`archive/refs/tags/v<version>.zip`). No URL is relative and none pins a commit
SHA. The archive is GitHub's source zip. Unless the repository setting that adds
Git LFS objects to archives is on, LFS files inside it are pointers. The large
combined graphs are therefore offered as release assets too.

`build_release_assets.py` stamps `dcat:byteSize` and `spdx:checksum` on each
distribution whose download is a built asset, including `chunks.jsonl.gz`.

## Validation

`tests/test_issue_710_dcat_ap.py` parses the record with rdflib and checks the
contract above. It also runs the official SEMIC DCAT-AP 3.0.1 SHACL shapes,
which are cached in `data/dcat-ap/` (see the README there). To run them
directly:

```bash
ESTLEG_ALLOW_KRR_WRITES=1 .venv/bin/python -m pytest -q tests/test_issue_710_dcat_ap.py
```

The `CORPUS_BUDGETS` table in `src/estleg/check_rt_staleness.py` holds each
distribution's frequency, and `tests/test_issue_531_staleness.py` keeps the
record equal to it.

## Maintainer actions outside git

- **andmed.eesti.ee publisher account.** Request a non-government publisher
  account on andmed.eesti.ee, then register `metadata.jsonld` (or the
  `metadata.jsonld` release asset) for harvesting. This step needs the portal
  operator and cannot be closed in the repository.
- **Licence election.** The compilation layer stays CC BY 4.0. RIA recommends
  CC0. Switching is the data owner's decision after the `NOTICE` sign-off.
- **Publisher type.** The record does not set an ADMS publisher type
  (`dcterms:type`). Pick one when the portal account is created, since the
  portal's account category may decide it.
