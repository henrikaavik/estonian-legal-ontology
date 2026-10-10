# Andmekirjeldus: Eesti õigusontoloogia

**Seis:** 8. oktoober 2026 · **Ontoloogia versioon:** 1.0.0
(`owl:versionIRI` <https://w3id.org/estleg/1.0.0>) · **Keel:** eesti keel
(ingliskeelsed lähtedokumendid on viidatud iga jao juures)

See dokument on mõeldud juristidele ja otsustajatele, kes hindavad, kas ja
kuidas andmestikku kasutada. Tehniline juhend on [README](../README.md),
käitajate juhend [OPERATOR_RUNBOOK.md](OPERATOR_RUNBOOK.md) ja visuaalne
ülevaade [eesti-oigusontoloogia-ulevaade.html](https://henrikaavik.github.io/estonian-legal-ontology/eesti-oigusontoloogia-ulevaade.html)
(GitHub Pages; avaldamine ootab hoidla seadistust, vt jagu 10).

## 1. Mis see andmestik on ja mis see ei ole

Eesti õigusontoloogia on masinloetav JSON-LD/RDF teadmusgraaf Eesti ja Euroopa
Liidu õigusest. See koondab avalikest allikatest seadused, määrused, eelnõud,
Riigikohtu lahendid ning EL õigusaktid ja kohtulahendid ühte nimeruumi
(`https://w3id.org/estleg/`) ning lisab nende vahele seoseid.

Andmestik **ei ole ametlik õigusallikas**. Kehtiv ja ametlik tekst on Riigi
Teatajas, EUR-Lexis ja kohtute ametlikes andmebaasides. Andmestik on
erialgatusena koostatud koopia ja analüüsikiht, mitte riigi infosüsteem.
Enne õiguslikku tugitegevust kontrolli teksti ametlikust allikast; iga akti
sõlm kannab viidet allikale (`dcterms:source`), kui see on olemas.

## 2. Katvus

Arvud on mõõdetud 8. oktoobril 2026 hoidla indeksfailidest
(`krr_outputs/INDEX.json` ja alamkorpuste `*_INDEX.json`). Kuupäev veerus
„Seisuga“ on indeksi tempel, mitte õigusteksti kehtivuskuupäev (vt jagu 4).

| Korpus | Kirjeid | Seisuga | Märkus |
|---|---:|---|---|
| Seadused (Riigi Teataja) | 1 127 | 2026-10-09 | 1 200 seadusefaili; mitmeosalised seadused on jagatud osadeks. 365 välislepingu ratifitseerimise või ühinemise seadust ja 1 muu kirje on ilma sätete tekstita. |
| Riigi määrused | 3 893 | 2026-10-09 | Neist 3 890 kehtivad; 3 olid seisukuupäevaks kehtetud. |
| KOV määrused | 11 845 | 2026-10-09 | Neist 11 789 kehtivad; 357 väljaandjat. Laaditakse eraldi. |
| Riigikogu otsused | 253 | 2026-06-01 | Pealkirjaindeks; sisuga kirjeid on 12. |
| Presidendi seadlused | 123 | 2026-06-01 | Pealkirjaindeks; sisuga kirjeid on 12. |
| Eelnõud (EIS) | 22 832 | 2026-03-07 | Avalik konsultatsioon 122, kooskõlastamine 13 670, valitsusele esitatud 9 040. |
| Riigikohtu lahendid | 12 104 | 2026-03-03 | Aastad 1993–2026; täistekst on 10 941 lahendil (tempel 2026-05-26). |
| Maa-, haldus- ja ringkonnakohtu lahendid | 1 | 2026-06-01 | Ainult näidis, mitte korpus (`estleg:isSampleData`). |
| EL õigusaktid (EUR-Lex) | 33 242 | 2026-03-03 | Neist 12 131 kehtivad; 206 on seotud Eesti ülevõtuga. |
| EL kohtulahendid (CURIA) | 22 290 | 2026-03-03 | Euroopa Kohus, Üldkohus, Avaliku Teenistuse Kohus. |

Kataloogis on kokku 28 624 JSON/JSON-LD faili; valideerija valib neist
kontrolliks 28 572. Ühte kuupäeva kogu andmestikule ei ole: iga korpus
uueneb eraldi.

Allolev plokk kannab samu arve masinloetaval kujul. Test
`tests/test_public_surface_docs.py` kontrollib, et need vastavad indeksitele.

<!-- estleg:andmed-counts -->
```json
{
  "measuredOn": "2026-10-09",
  "ontologyVersion": "1.0.0",
  "versionIRI": "https://w3id.org/estleg/1.0.0",
  "snapshotDate": "2026-10-09",
  "lawFileCount": 1200,
  "corpora": [
    {"key": "laws", "index": "krr_outputs/INDEX.json", "countField": "total_laws", "count": 1127, "stampField": "generated", "stamp": "2026-10-09"},
    {"key": "regulations-riik", "index": "krr_outputs/regulations/riik/REGULATIONS_RIIK_INDEX.json", "countField": "totalRegulations", "count": 3893, "stampField": "kehtiv", "stamp": "2026-10-09"},
    {"key": "regulations-kov", "index": "krr_outputs/regulations/kov/REGULATIONS_KOV_INDEX.json", "countField": "totalRegulations", "count": 11845, "stampField": "kehtiv", "stamp": "2026-10-09"},
    {"key": "resolutions-otsus", "index": "krr_outputs/resolutions/RESOLUTIONS_INDEX.json", "countField": "kinds.otsus.rt_total", "count": 253, "stampField": "generated", "stamp": "2026-06-01"},
    {"key": "resolutions-seadlus", "index": "krr_outputs/resolutions/RESOLUTIONS_INDEX.json", "countField": "kinds.seadlus.rt_total", "count": 123, "stampField": "generated", "stamp": "2026-06-01"},
    {"key": "eelnoud", "index": "krr_outputs/eelnoud/EELNOUD_INDEX.json", "countField": "total_drafts", "count": 22832, "stampField": "generated", "stamp": "2026-03-07"},
    {"key": "riigikohus", "index": "krr_outputs/riigikohus/RIIGIKOHUS_INDEX.json", "countField": "total_decisions", "count": 12104, "stampField": "generated", "stamp": "2026-03-03"},
    {"key": "riigikohus-full-text", "index": "krr_outputs/riigikohus/RIIGIKOHUS_INDEX.json", "countField": "full_text.decisions_with_full_text", "count": 10941, "stampField": "full_text.generated", "stamp": "2026-05-26"},
    {"key": "kohtud-sample", "index": "krr_outputs/kohtud/KOHTUD_INDEX.json", "countField": "ingested", "count": 1, "stampField": "generated", "stamp": "2026-06-01"},
    {"key": "eurlex", "index": "krr_outputs/eurlex/EURLEX_INDEX.json", "countField": "total_acts", "count": 33242, "stampField": "generated", "stamp": "2026-03-03"},
    {"key": "eurlex-in-force", "index": "krr_outputs/eurlex/EURLEX_INDEX.json", "countField": "lens.in_force", "count": 12131, "stampField": "generated", "stamp": "2026-03-03"},
    {"key": "curia", "index": "krr_outputs/curia/CURIA_INDEX.json", "countField": "total_decisions", "count": 22290, "stampField": "generated", "stamp": "2026-03-03"}
  ]
}
```

## 3. Allikad

| Allikas | Mida sealt võetakse | Ligipääs |
|---|---|---|
| Riigi Teataja | Seadused, riigi ja KOV määrused, Riigikogu otsused, seadlused | Otsingu-API `api/oigusakt_otsing/1/otsi`; akti XML ja metaandmed avalikust API-st `public-api/api/v1/akt/{id}` (alates RT uuendusest 1.06.2026) |
| Riigi Teataja kohtulahendid | Alama astme kohtulahendite näidis | `api/v1/kohtuteave/otsing/kohtulahendid` |
| Eelnõude infosüsteem (EIS) | Eelnõud kolmes menetlusfaasis | RSS-vood aadressil `eelnoud.valitsus.ee` |
| RIK / Riigikohus | Riigikohtu lahendite loend, kokkuvõtted ja täistekst | `rikos.rik.ee` |
| EUR-Lex / CELLAR (Väljaannete Talitus) | EL määrused, direktiivid, otsused eesti keeles | SPARQL `publications.europa.eu/webapi/rdf/sparql` |
| CURIA (EUR-Lexi kaudu) | Euroopa Kohtu, Üldkohtu ja Avaliku Teenistuse Kohtu lahendid | Sama SPARQL-teenus |
| Õiguskantsler | Seisukohad annotatsioonidena | `oiguskantsler.ee` |
| Statistikaamet (EHAK) ja haldusreformi järglusseosed | KOV üksused ja ajalooliste väljaandjate sidumine | Hoidla failid `data/ehak/` |
| EuroVoc | Teemaklassifikaatori mõisted | Väljaannete Talituse sõnastik |

Kõik allikad on avalikud. Andmestik ei kasuta isikustatud ega
piiratud ligipääsuga kanaleid.

## 4. Värskus

Andmestik on hetkeseis, mitte reaalajas koopia. Värskuse kohta on kolm
erinevat kuupäeva:

- **`estleg:kehtiv`** on kuupäev, mille seisuga akti tekst kehtis. Seaduste
  failides on see 2026-05-24 (1 018 faili; ülejäänud 177 failil tempel
  puudub, sest need on sisuta kirjed või pärandfailid). Määruste seisuga
  kuupäev on 2026-05-01.
- **Indeksi tempel** on kuupäev, millal korpus viimati kokku pandi (tabel jaos 2).
- **`dcterms:modified`** kataloogis `metadata.jsonld` näitab kataloogi
  muutmist, mitte õigusteksti uuendamist.

Avaldatud uuendussagedus (`dcterms:accrualPeriodicity`) on kogu andmestikule
igakuine. Korpuste kaupa kehtivad järgmised lubatud mahajäämused
(`src/estleg/check_rt_staleness.py`, `CORPUS_BUDGETS`):

| Korpus | Sagedus | Lubatud mahajäämus |
|---|---|---|
| Seadused | igakuine | 45 päeva |
| Riigi ja KOV määrused | igakuine | 60 päeva |
| Eelnõud | igakuine | 60 päeva |
| Riigikohus ja alama astme näidis | kord kvartalis | 120 päeva |
| EUR-Lex ja CURIA | kord kvartalis | 120 päeva |

**8. oktoobri 2026 seisuga ei täida ükski korpus oma värskuse eesmärki.**
Seaduste tekst on 137 päeva ja määrused 160 päeva vanad; eelnõud, Riigikohus,
EUR-Lex ja CURIA on 215–219 päeva vanad. Värskuskontroll
`python3 scripts/check_rt_staleness.py` annab praegu vea. Enne
otsustamist eelda, et andmestikus ei ole viimaste kuude muudatusi.

## 5. Ametlik tekst ja tuletatud kihid

Andmestikus on kaks eri laadi sisu ning neid tuleb eristada.

**Allikast pärit tekst ja metaandmed.** Akti pealkiri, sätete tekst
(`estleg:legalText`), kehtivuskuupäevad, CELEX ja ECLI tunnused ning
allikaviited on võetud allikast masinlikult. Need ei ole ametlik väljaanne:
parsimisel võib struktuur (osa, peatükk, lõige) erineda ametlikust esitusest.

**Tuletatud ja heuristilised kihid.** Need on projekti enda
masinlikud järeldused ja **ei ole õiguslikud seisukohad**:

- ristviited ja vastupidised viited sätete vahel;
- Riigikohtu lahendi seos konkreetse sättega;
- direktiivide ülevõtu ja naaberriikide harmoneerimise seosed;
- EuroVoc teemad, deontiline liik (kohustus, õigus, luba, keeld), sihtrühm;
- pädevad asutused, sanktsioonid, mõisted ja sarnasusseosed.

Klassifikaatorite väljundil on usaldusväärsuse näitaja
`estleg:assertionConfidence` (0–1). Inimese parandatud väärtus on lukustatud
failis `data/heuristic_overrides.jsonl` ja kannab märget
`prov:wasAttributedTo` ([HEURISTIC_OVERRIDES.md](HEURISTIC_OVERRIDES.md)).
Puudumist märkivad lipud, näiteks `estleg:competentAuthorityNotExtracted` või
`estleg:noTranspositionEdgeInCorpus`, tähendavad ainult, et seost ei leitud
selles korpuses. Need ei ole õiguslik järeldus
([SCHEMA_REFERENCE.md](SCHEMA_REFERENCE.md)). Märge `estleg:isStubNode` on
tehniline koostemärge, mitte väide päris objekti kohta.

Omaduste stabiilsusastmed (stabiilne, lisanduv, heuristiline) on kirjas
dokumendis [STABILITY.md](STABILITY.md). Heuristiliste kihtide õigusliku
täpsuse mõõdetud lävendid puuduvad veel (töö #698). Arhitektuur ja
koostamise järjekord: [ARCHITECTURE.md](ARCHITECTURE.md).

## 6. Isikuandmed

Lähtedokument: [DATA_PROTECTION.md](DATA_PROTECTION.md) (inglise keeles).
See on 8. oktoobri 2026 seisuga isikuandmete töötlemise register GDPR artikli 30
eeskujul. Register kirjeldab, milliseid andmeid töödeldakse, kust need
pärinevad ja kuidas neid eemaldatakse. Neli otsust on veel lahtised (vt allpool).

- **Riigikohus** (`krr_outputs/riigikohus/`, 12 104 lahendit). Kokkuvõtetes ja
  täistekstis võivad esineda isikute nimed. Kriminaalasjades on süüdistus
  seotud tuvastatava isikuga. Süütegusid ja süüdimõistmisi puudutavatele
  andmetele kehtib isikuandmete kaitse üldmääruse (GDPR) **artikkel 10**.
- **CURIA** (`krr_outputs/curia/`, 22 290 lahendit). Menetlusosaliste nimed on
  lahendi pealkirjas (`rdfs:label`).
- **Alama astme kohtud** (`krr_outputs/kohtud/`). Praegu on ainult üks
  näidislahend ilma kokkuvõtteta.
- **Isikukoodid on eemaldatud.** Riigikohtu lahenditest asendati 27 isikukoodi
  21 lahendis tekstiga `[isikukood eemaldatud]`. Valideerija keelab
  isikukoodi avaldamise. **Nimesid ei ole eemaldatud**; see küsimus on
  lahtine (#720).
- Kataloogis on Riigikohtu ja CURIA jaotused tähistatud
  `estleg:containsPersonalData: true`.

Projekti tööhüpotees on õiguslik alus GDPR artikli 6 lõike 1 punkti f
(õigustatud huvi) järgi; punkti e kasutamist peetakse ebatõenäoliseks, sest
projekt ei ole avaliku võimu kandja. Hoidla haldaja või andmekaitsespetsialisti
otsust ootavad:

1. vastutava töötleja määramine;
2. nimede käsitlus (säilitada, pseudonüümida või eemaldada); andmekaitsespetsialisti
   otsus on avatud 8. oktoobril 2026;
3. artikli 6 alus koos huvide tasakaalustamise testiga ning artikli 10 alus
   kriminaalasjade sisule;
4. taasisikustamise kontrolli tegemine ametliku allika `rikos.rik.ee` vastu.

**Andmesubjekti pöördumised** (juurdepääs, kustutamine, vastuväide) käivad
[SECURITY.md](../SECURITY.md) jaotise „How to report“ kaudu. Isiklikku
e-postiaadressi ei avaldata.

**Taaskasutaja vastutus.** Kes laadib alla ja avaldab uuesti Riigikohtu või
CURIA alamkorpuse, muutub nende isikuandmete **iseseisvaks vastutavaks
töötlejaks**. Tal peab olema oma õiguslik alus ning ta vastutab ise teavitamise
ja andmesubjekti õiguste (juurdepääs, kustutamine, vastuväide) eest. Projekti
seisukoht ei laiene taaskasutajale. Kaalu, kas sinu kasutusjuht vajab nimesid.

## 7. Õigused ja litsents

Lähtedokumendid: [DATA_RIGHTS.md](DATA_RIGHTS.md), [NOTICE](../NOTICE),
[LICENSE](../LICENSE). Õiguste kirjeldus on **eelnõu, mis ootab
õiguslikku kinnitust**.

- **Tarkvara** (`src/`, `scripts/`, `mcp_server/`, `tests/`) on MIT litsentsiga.
  MIT ei kata andmeid.
- **Kolmandate isikute tekstid** jäävad oma allika tingimuste alla. Eesti
  õigusaktid, eelnõud ja kohtulahendid ei ole autoriõiguse objektid
  (autoriõiguse seaduse § 5). Riigi Teataja konsolideeritud masinloetavale
  tootele ja andmebaasile võivad siiski kehtida Riigi Teataja
  kasutustingimused ja andmebaasi sui generis õigus. Neid tingimusi ei ole veel
  kinnitatud ning hulgi taasavaldamist ei saa pidada lubatuks. EL materjal on
  © Euroopa Liit ja seda kasutatakse komisjoni otsuse 2011/833/EL alusel,
  allikale viidates.
- **Projekti enda kiht** (valik ja korraldus, seosed, `estleg:` IRI-d,
  ontoloogia, sõnastikud, tuletatud klassifikatsioonid) on pakutud
  litsentsiga **CC BY 4.0** (`CITATION.cff`). See ei anna õigusi kolmandate
  isikute tekstidele. Taaskasutaja peab järgima mõlemat kihti.

## 8. Viitamine

Viita konkreetsele versioonile, mitte hoidla `main` harule. Masinloetav kirje
on failis [CITATION.cff](../CITATION.cff).

```text
Aavik, H. (2026). Estonian Legal Ontology (versioon 1.0.0) [andmestik].
https://w3id.org/estleg/1.0.0
```

Päringutes ja andmevahetuses seo andmed versiooni IRI-ga
`owl:versionIRI` <https://w3id.org/estleg/1.0.0>. Versioonimata `estleg:` IRI
ei ole püsiv võõrvõti erinevate hoidlakoopiate vahel. Zenodo DOI on veel
loomata.

## 9. Versioon

Viimane väljalase on **1.0.0** (`owl:versionInfo`), avaldatud 19. augustil
2026 sildiga `v1.0.0`. Pärast seda tehtud parandused põhiharus ei ole veel uus
väljalase. Väljalaskekord: [RELEASE.md](RELEASE.md); kvaliteediseis:
[VALIDATION_REPORT.md](VALIDATION_REPORT.md).

## 10. Kontakt ja lahtised otsused

- **Küsimused, vead ja parandusettepanekud:** GitHubi teemad
  <https://github.com/henrikaavik/estonian-legal-ontology/issues>.
- **Turvaprobleemid ja isikuandmete päringud**, mida ei tohi avalikult
  postitada: [SECURITY.md](../SECURITY.md) kirjeldab, kuidas teatada
  privaatselt. Ära lisa selliseid üksikasju avalikku teemasse.

Hoidla haldaja otsust ootavad:

1. GitHub Pagesi sisselülitamine (hoidla seadistus), et ülevaade avaneks
   aadressil `henrikaavik.github.io/estonian-legal-ontology/`.
2. Jaos 6 loetletud neli isikuandmete otsust: vastutav töötleja, nimede
   käsitlus, artikli 6 ja 10 alus ning taasisikustamise kontroll (#720).
3. Õiguste kirjelduse ja CC BY 4.0 valiku kinnitamine ning Riigi Teataja
   kasutustingimuste kontroll.
4. Korpuste värskendamine, et täita jaos 4 kirjeldatud värskuse eesmärki.
