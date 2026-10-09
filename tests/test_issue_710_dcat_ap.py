"""#710 — ``metadata.jsonld`` is a DCAT-AP 3.0.1 / Andmekirjelduse standard record.

Parses the committed catalogue with rdflib (as a harvester would) and pins the
structural contract so it cannot regress: a ``dcat:Catalog`` wrapper, an
organisation publisher, a contact point with an e-mail, a licence and access
rights on every distribution that match the layered rights in ``NOTICE``,
absolute URLs pinned to the release tag, and EU authority IRIs. The last test
runs the official SEMIC DCAT-AP 3.0.1 SHACL shapes cached in ``data/dcat-ap/``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rdflib import RDF, Graph, Literal, Namespace, URIRef

from estleg.estleg_common import ONTOLOGY_VERSION

REPO = Path(__file__).resolve().parent.parent
METADATA = REPO / "metadata.jsonld"
DCAT_AP_DIR = REPO / "data" / "dcat-ap"

DCAT = Namespace("http://www.w3.org/ns/dcat#")
DCT = Namespace("http://purl.org/dc/terms/")
FOAF = Namespace("http://xmlns.com/foaf/0.1/")
VCARD = Namespace("http://www.w3.org/2006/vcard/ns#")

DATASET = URIRef("https://w3id.org/estleg/dataset/estonian-legal-ontology")
CATALOG = URIRef("https://w3id.org/estleg/catalog")
EU = "http://publications.europa.eu/resource/authority/"
CC_BY = URIRef(f"{EU}licence/CC_BY_4_0")
COM_REUSE = URIRef(f"{EU}licence/COM_REUSE")
LAYERED = URIRef(f"{DATASET}#licence-layered")
PUBLIC = URIRef(f"{EU}access-right/PUBLIC")
DCAT_AP_301 = URIRef("https://semiceu.github.io/DCAT-AP/releases/3.0.1/")
REPO_URL = "https://github.com/henrikaavik/estonian-legal-ontology"
MAINTAINER_MAILTO = URIRef("mailto:henrik.aavik@gmail.com")

# Licence per distribution title, mirroring NOTICE / docs/DATA_RIGHTS.md:
# EU material carries the source's reuse terms; Estonian-source material
# (not an object of copyright, § 5 AutÕS) carries the project's CC BY 4.0
# offer for its compilation layer; the all-sources tree has no single
# licence and points at the layered-terms document.
EXPECTED_LICENCE = {
    "JSON-LD ontology files (complete dataset)": LAYERED,
    "Combined enacted laws ontology": CC_BY,
    "Domestic regulations (state-level)": CC_BY,
    "Combined draft legislation ontology": CC_BY,
    "Combined EU legislation ontology": COM_REUSE,
    "Combined EU court decisions ontology": COM_REUSE,
    "Combined Supreme Court decisions ontology (Riigikohus)": CC_BY,
    "Municipal regulations ontology (KOV)": CC_BY,
    "Inter-release change record 0.11.0": CC_BY,
    "Retrieval chunks (provision-version JSON Lines)": CC_BY,
}


@pytest.fixture(scope="module")
def graph() -> Graph:
    return Graph().parse(str(METADATA), format="json-ld")


def _distributions(graph: Graph) -> dict[str, URIRef]:
    out: dict[str, URIRef] = {}
    for dist in graph.objects(DATASET, DCAT.distribution):
        title = graph.value(dist, DCT.title)
        assert title is not None, f"{dist} has no dcterms:title"
        out[str(title)] = dist
    return out


def test_catalog_wraps_the_dataset(graph: Graph) -> None:
    catalogs = set(graph.subjects(RDF.type, DCAT.Catalog))
    assert catalogs == {CATALOG}
    assert (CATALOG, DCAT.dataset, DATASET) in graph
    titles = {t.language for t in graph.objects(CATALOG, DCT.title)}
    assert {"et", "en"} <= titles
    assert graph.value(CATALOG, DCT.description) is not None
    assert graph.value(CATALOG, FOAF.homepage) == URIRef(REPO_URL)
    assert graph.value(CATALOG, DCT.publisher) is not None
    assert URIRef(f"{EU}language/EST") in set(graph.objects(CATALOG, DCT.language))
    assert DCAT_AP_301 in set(graph.objects(CATALOG, DCT.conformsTo))


def test_publisher_is_an_organisation_and_creator_the_person(graph: Graph) -> None:
    publisher = graph.value(DATASET, DCT.publisher)
    assert publisher == graph.value(CATALOG, DCT.publisher)
    assert (publisher, RDF.type, FOAF.Organization) in graph
    assert graph.value(publisher, FOAF.name) is not None
    assert graph.value(publisher, FOAF.homepage) == URIRef(REPO_URL)
    creator = graph.value(DATASET, DCT.creator)
    assert creator == URIRef("https://github.com/henrikaavik")
    assert (creator, RDF.type, FOAF.Person) in graph


def test_contact_point_has_email(graph: Graph) -> None:
    contact = graph.value(DATASET, DCAT.contactPoint)
    assert contact is not None
    assert (contact, RDF.type, VCARD.Kind) in graph
    assert graph.value(contact, VCARD.fn) is not None
    assert graph.value(contact, VCARD.hasEmail) == MAINTAINER_MAILTO
    assert graph.value(contact, VCARD.hasURL) is not None


def test_dataset_carries_no_blanket_licence(graph: Graph) -> None:
    # #545/#684: the dataset as a whole is not licensable by the project.
    assert graph.value(DATASET, DCT.license) is None
    assert graph.value(DATASET, DCT.accessRights) == PUBLIC


def test_every_distribution_has_the_layered_licence(graph: Graph) -> None:
    dists = _distributions(graph)
    assert set(dists) == set(EXPECTED_LICENCE)
    for title, dist in dists.items():
        licences = set(graph.objects(dist, DCT.license))
        assert licences == {EXPECTED_LICENCE[title]}, title
        assert (EXPECTED_LICENCE[title], RDF.type, DCT.LicenseDocument) in graph
        assert graph.value(dist, DCT.accessRights) == PUBLIC, title
        rights = graph.value(dist, DCT.rights)
        assert rights is not None, f"{title}: no dcterms:rights statement"
        assert (rights, RDF.type, DCT.RightsStatement) in graph, title
        assert graph.value(rights, URIRef("http://www.w3.org/2000/01/rdf-schema#label")), title


def test_distributions_have_identifier_format_and_frequency(graph: Graph) -> None:
    for title, dist in _distributions(graph).items():
        assert isinstance(graph.value(dist, DCT.identifier), Literal), title
        fmt = graph.value(dist, DCT["format"])  # DCT.format is str.format
        assert fmt is not None and str(fmt).startswith(f"{EU}file-type/"), title
        media = graph.value(dist, DCAT.mediaType)
        if media is not None:
            assert str(media).startswith("http://www.iana.org/assignments/media-types/"), title
        freq = graph.value(dist, DCT.accrualPeriodicity)
        assert freq is not None and str(freq).startswith(f"{EU}frequency/"), title
    dataset_freq = graph.value(DATASET, DCT.accrualPeriodicity)
    assert dataset_freq == URIRef(f"{EU}frequency/MONTHLY")


def test_urls_are_absolute_and_pinned_to_the_release_tag(graph: Graph) -> None:
    tag = f"v{ONTOLOGY_VERSION}"
    for title, dist in _distributions(graph).items():
        access = graph.value(dist, DCAT.accessURL)
        download = graph.value(dist, DCAT.downloadURL)
        assert access is not None and download is not None, title
        for url in (str(access), str(download)):
            assert url.startswith("https://"), (title, url)
            if url.startswith(REPO_URL):
                assert f"/{tag}/" in url or url.endswith(f"/{tag}.zip"), (title, url)


def test_theme_spatial_temporal_and_landing_page(graph: Graph) -> None:
    themes = set(graph.objects(DATASET, DCAT.theme))
    assert {URIRef(f"{EU}data-theme/JUST"), URIRef(f"{EU}data-theme/GOVE")} <= themes
    assert graph.value(DATASET, DCT.spatial) == URIRef(f"{EU}country/EST")
    assert graph.value(DATASET, DCT.temporal) is not None
    assert graph.value(DATASET, DCAT.landingPage) == URIRef(REPO_URL)


def test_schema_prefix_matches_the_controlled_vocabulary() -> None:
    meta = json.loads(METADATA.read_text(encoding="utf-8"))
    vocab = json.loads(
        (REPO / "krr_outputs" / "controlled_vocabulary.jsonld").read_text(encoding="utf-8")
    )
    assert meta["@context"]["schema"] == vocab["@context"]["schema"] == "https://schema.org/"


def test_record_conforms_to_dcat_ap_301_shacl(graph: Graph) -> None:
    pyshacl = pytest.importorskip("pyshacl")
    from rdflib.namespace import SH

    shapes = Graph()
    for name in ("dcat-ap-SHACL.ttl", "ranges.ttl"):
        shapes.parse(str(DCAT_AP_DIR / name), format="turtle")
    # The upstream 3.0.1 release lists seven sh:property IRIs it never
    # defines (no sh:path); pyshacl refuses those, so drop the dangling links.
    dangling = [
        (shape, prop)
        for shape, prop in shapes.subject_objects(SH.property)
        if (prop, SH.path, None) not in shapes
    ]
    for shape, prop in dangling:
        shapes.remove((shape, SH.property, prop))
    conforms, _, text = pyshacl.validate(
        graph, shacl_graph=shapes, inference="none", advanced=True
    )
    assert conforms, text
