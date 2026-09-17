<!-- Reviewer worksheet from the 2026-09-03 public-sector readiness review. Raw evidence with file:line citations; the consolidated position is docs/PUBLIC_SECTOR_REVIEW_2026-09.md, which supersedes this file where they disagree (e.g. the w3id PURL resolves since 2026-08-19). Tree reviewed: main @ c96577d50c. -->

# Estonian public-sector legal-data landscape (research as of 2026-09-03)

Scope: facts a public body would check before adopting the Estonian Legal Ontology
(`https://w3id.org/estleg/`, estleg-mcp). Every bullet carries a URL and, where known,
a date. "Verified" means I fetched or queried the endpoint on 2026-09-03.
"Unconfirmed" means the claim comes from a secondary page or could not be retrieved.

---

## 1. Riigi Teataja (RT) and RIK: machine-readable interfaces, plans, terms

- **New RT platform went live 1 June 2026**, replacing the 2010 version
  (JDM news, 25 May 2026): autocomplete, a visual timeline of how a statute's
  text changed, side-by-side version comparison, links to related Riigikohus
  decisions, copyable provision references. Beta was at
  `beeta.riigiteataja.ee`. Minister Liisa-Ly Pakosta's stated next step is AI:
  a user should be able to ask "give me all norms I must follow for
  scenario X" and get statutes, local regulations, explanations and court
  guidance compiled.
  https://www.justdigi.ee/uudised/pane-tahele-riigi-teataja-kasutamine-1-juunist-mugavam
- **New public XML/JSON API since 1 June 2026** (RT FAQ; page is JS-rendered
  and states the XML format changed slightly with the move to S3 storage):
  - `https://www.riigiteataja.ee/public-api/api/v1/akt/{id}/xml` — verified
    2026-09-03: HTTP 200, `application/octet-stream`, schema
    `tyviseadus_1_10.02.2010`, 138 KB for the Constitution (id 111042025003).
  - `https://www.riigiteataja.ee/public-api/api/v1/akt/{id}` — verified:
    JSON metadata (`aktiStaatus`, `kehtivuseAlgus`, `lyhend`, `liigitused`,
    `tolkeSeosId`, related-court-decisions URL prefix).
  - The legacy `/akt/{id}.xml` now returns the Angular app shell (HTML), not
    XML. No OpenAPI/Swagger was found (`/public-api/v3/api-docs` and
    `/public-api/swagger-ui/` are 404). **No published rate limits or API
    terms were found.** FAQ: https://www.riigiteataja.ee/kkk.html
- **Bulk open data** at https://www.riigiteataja.ee/avaandmed/ERT/ —
  verified: `xml.2024.zip` 344 MB, `xml.2025.zip` 2.6 GB, `xml.2026.zip`
  42.6 GB plus `.en` English variants and MD5/SHA sums, all regenerated
  **30 Aug 2026** (`LOEMIND.en.txt`: "ingliskeelsed avaandmed on genereeritud
  2026-08-30 seisuga"). No licence file in the directory.
- **ELI**: RT serves ELI-shaped URIs (e.g.
  `https://www.riigiteataja.ee/eli/ee/515032023008/consolide`), but the
  EUR-Lex ELI implementation table (last updated 19 May 2025) marks Estonia
  for **pillar 1 (identifier) only**; no metadata/ontology, no semantic-web
  publication, no metadata sync. Verified: the `/eli/.../consolide` page HTML
  carries no ELI `<meta>`/RDFa.
  https://eur-lex.europa.eu/content/eli-register/implementation.html
- **Ownership and terms**: RIHA lists RT's owner as Justiits- ja
  Digiministeerium (code 70000898), status IN_USE, `development_status:
  IN_DEVELOPMENT`, record updated 2025-05-08 (verified via
  `https://www.riha.ee/api/v1/systems/rt`). RIK hosts and operates it
  technically: https://www.rik.ee/en/other-services/state-gazette.
  Legal texts fall outside copyright under Autoriõiguse seadus § 5
  (õigusaktid ja haldusdokumendid): https://www.riigiteataja.ee/akt/AutÕS.
  Reuse is governed by Avaliku teabe seadus: https://www.riigiteataja.ee/akt/AvTS.
- Older context: the 2012 "eelnõude otsing" already joins Riigikogu (EMS) and
  government (EIS) draft statuses inside RT:
  https://www.justdigi.ee/uudised/riigi-teataja-uus-lahendus-hoiab-kursis-oigusaktide-arengutega

**Implication for this project.** Ingest should move to the `public-api`
XML endpoint plus the yearly zips and pin the post-June-2026 schema; nothing
official constrains crawl rate, but the absence of terms is itself a risk to
document. The ELI gap is the opening: estleg can be the de-facto pillar-2/3
implementation for Estonia by publishing ELI-ontology metadata with
`owl:sameAs` to RT's ELI URIs. Pakosta's "all norms for a scenario"
vision is exactly what the graph plus MCP already answers (laws + KOV
regulations + court links), which is the pitch line to JDM's RT unit.

---

## 2. Justiits- ja Digiministeerium (JDM): drafting policy and tooling

- **HÕNTE**: in force since 2012; substantively amended once, spring 2025,
  making the *halduskoormuse tasakaalustamise reegel* ("one in, one out")
  mandatory; guide published June 2025:
  https://www.justdigi.ee/sites/default/files/documents/2025-06/Halduskoormuse%20tasakaalustamise%20juhis.pdf.
  HÕNTE käsiraamat last updated 2 July 2026:
  https://www.justdigi.ee/oigusloome-arendamine/hea-oigusloome-ja-normitehnika/honte-kasiraamat
- **HÕNTE amendment VTK, 23 Oct 2025** (seven discussion points; timeline:
  Nov 2025 consultation, Dec 2025 draft; contact Katariina Kärsten). Items
  relevant here: (a) ex-post evaluation is failing — only 31 bills since 2016
  planned a järelhindamine; (b) **§ 6.3 proposes mandatory correspondence
  tables also for EU *regulations*, not only directives**, and § 6.1 requires
  stating the harmonisation level and Member-State options taken; (c) AI:
  "Mõju hindamise süsteemi edasiarendamisel tuleb arvestada tehisaru
  kasutamise võimalusega" (AI-checked impact checklist, HÕNTE-conformance
  check of the seletuskiri, AI summaries of risks and affected groups).
  Metrics: 32 VTKs in 2024; 41% of 2024 bills preceded by a VTK. Stakeholders
  listed include ELVL, Tartu Ülikool õigusteaduskond, RAKE, Nurkse institute.
  https://adr-docs.karlerss.com/K5uAli9S9hAZIA5NcXxo9lxnAUIACfTa/20251023%20H%C3%95NTE%20muutmise%20VTK.pdf
  (mirror of a Kaitseministeerium document-register file)
- **Mõjude hindamise metoodika** (2021 edition) is being updated jointly by
  JDM and Riigikantselei alongside HÕNTE; OECD ranks Estonia 4th of 37 on
  RIA systems.
  https://www.justdigi.ee/oigusloome-arendamine/hea-oigusloome-ja-normitehnika/oigustloovate-aktide-mojude-hindamine
- **EIS replacement = "Riigi koosloome keskkond", database name "Sätla"**:
  joint JDM + Riigikantselei + Riigikogu Kantselei IT solution (named
  Nov 2020, piloting since 2021, Public Sector Innovation Programme funding).
  Riigikogu adopted the *Riigi Teataja seaduse muutmise seadus* (879 SE) on
  **10 June 2026** (Riigikogu API: `activeDraftStatus:
  AVALDATUD_RIIGITEATAJAS`, 2026-07-03, lead committee Põhiseaduskomisjon).
  It creates the legal basis for Sätla covering the whole lifecycle "alates
  algatusest kuni Riigi Teatajas avaldamiseni", will **replace EIS**, first
  stage in use from **1 Oct 2026**, framed as a move "from document-centric
  to data-driven legislative processes", saving an estimated 1,855 working
  days/year.
  https://www.justdigi.ee/oigusloome-arendamine/riigi-koosloome-keskkond ;
  https://valitsus.ee/uudised/valitsuse-9426-istungi-kommenteeritud-paevakord ;
  https://www.riigikogu.ee/istungi-ulevaated/riigikogu-vottis-vastu-kaheksa-seadust-ja-uhe-otsuse/
- **Regulations revision (bureaucracy reduction)**: all **4,009**
  government/ministerial regulations in RT as of 1 May 2025 were reviewed;
  327 flagged for administrative-burden cuts, **304 obsolete regulations to
  be repealed**, 121 to be simplified; ~750 total (≈ one fifth of all
  regulations). By 5 Mar 2026: 41% executed, 17% kept unchanged, 56 repealed
  by government; some repeals wait for *volitusnorm* changes in laws.
  https://www.justdigi.ee/uudised/riigi-maaruste-revisjon-puhastab-oigusruumi-ja-teeb-inimeste-elu-lihtsamaks ;
  https://valitsus.ee/uudised/valitsuse-kabinetinoupidamise-paevakord-5-marts-2026
- **Eesti.ai project 5 "Õigusloomeprotsessi kvaliteet ja tõhusus"** (owner
  JDM, approved April 2026): AI for analysing drafts, detecting conflicts and
  gaps. Sibling projects: 6 procurement, 7 AI governance in the public
  sector, 8 compute, 11 EKI Estonian LLM dataset. https://eesti.ai/projektid
- **"Rules as Code" / masintöödeldavus**: no Estonian government programme
  under that name was found. Nearest signals: Sätla's data-driven framing;
  the AI action plan's own gap note "Puuduvad selged standardid, kuidas
  õigusaktides sätestatut praktikas rakendada"
  (https://www.mkm.ee/media/10157/download); the civic tool *Apsakaleidja*
  by Luukas Ilves built on the Riigikogu API after the 2025 gambling-tax
  wording slip (https://www.popvox.org/blog/from-slip-up-to-solution,
  27 Jan 2026; https://lawlab.ilves.ai/); Pakosta on ERR, **3 Sep 2026**:
  MPs should let AI check bills, and already do so informally
  (https://www.err.ee/1609909858/...); March 2025: JDM preparing an amendment
  so AI may read public texts lacking an opt-out mark
  (https://news.err.ee/1609693070/...). Õigusloojate konverents,
  20 Nov 2025, asked "which stages of the legislative process could benefit
  most from AI".
  https://www.justdigi.ee/uudised/oigusloojate-konverents-toi-fookusesse-seadusloome-tuleviku-valjakutsed
- Governing policy: *Õigusloomepoliitika põhialused aastani 2030* (Riigikogu
  decision 12 Nov 2020; JDM reports annually to Riigikogu). The older
  *õigusloome mahu vähendamise kava* pages (2015–2018) are historical.
  https://www.justdigi.ee/oigusloome-arendamine/oiguspoliitika-pohialused-aastani-2030-0

**Implication for this project.** JDM's three live workstreams map directly
onto existing estleg layers: Sätla needs reference resolution and
draft-to-provision links (ELI-DL-ready identifiers); the regulations
revision needs "which regulations rest on a repealed or amended
*volitusnorm*" (amendment + cross-reference layers); HÕNTE § 6.3's
correspondence tables for EU regulations are the transposition layer
generalised to CELEX regulations. Eesti.ai project 5 is the funded buyer for
conflict/gap detection, and its owner sits in the same ministry that owns
RT.

---

## 3. Riigikogu Kantselei: open data and analytics interest

- **api.riigikogu.ee** (OpenAPI reports v2.21.8; verified 2026-09-03):
  `/api/volumes/drafts` (search by title, reference, type, initiator, dates)
  and `/api/volumes/drafts/{uuid}` (with stenography), `/api/documents`,
  `/api/events` (sittings, agenda), `/api/votings`, `/api/plenary-members`,
  `/api/usergroups` (committees, factions), `/api/files/{uuid}/download`,
  statistics, EuroVoc descriptors, stenogram texts; ET/EN/RU. Live check:
  `?title=Riigi Teataja` returns 879 SE with stage and status fields.
  https://api.riigikogu.ee/swagger-ui/index.html ;
  https://api.riigikogu.ee/v3/api-docs
- **Licence CC BY-SA 3.0; rate limit 1 request/s per IP and 12 requests/min
  per URL; data before 2012 may be defective**; issue tracker
  https://github.com/riigikogu-kantselei/api (28 open issues);
  https://www.riigikogu.ee/en/open-data/
- **Riigikogu is a co-owner of Sätla** (with JDM and Riigikantselei), so
  parliamentary drafting will move into the same data-centric environment.
- **Analytics interest**: the State Budget Control Select Committee held a
  public hearing on AI in the public sector on **15 Apr 2026** (invited:
  Luukas Ilves as Eesti.ai adviser, JDM, Riigikontroll), prompted by a
  National Audit Office review finding the state lacks an overview of AI
  systems in use. https://www.riigikogu.ee/pressiteated/180673/ ;
  video https://www.youtube.com/watch?v=QCQ4StyMRmc
- Riigikogu Toimetised 53/2026 (May 2026) carries a data-policy issue
  ("Andmed kui riukalik probleem"); content not reviewed.
  https://rito.riigikogu.ee/wordpress/wp-content/uploads/2026/05/RiTo_53_2026.pdf

**Implication for this project.** Key `ProposedAmendment` nodes on the
Riigikogu draft UUID and mark (e.g. `879 SE`) and reuse the API's EuroVoc
descriptors, which makes the drafts layer joinable with parliament's own
data. The CC BY-SA 3.0 share-alike term must be tracked in the licence
manifest for any derived draft data. A committee-facing "bill impact"
view (provisions, regulations, court decisions touched by a bill) is a
credible wedge given the April 2026 hearing and Pakosta's September 2026
call.

---

## 4. Bürokratt and the kratt / AI strategy

- **Bürokratt (RIA)**: in use at 20+ public bodies (RIA page), 18 agencies
  per Ilves (Apr 2026). **2025 roadmap**: large-language-model + RAG
  deployment ("suure keelemudeli (SKM/RAG) juurutus"), a general-knowledge
  module, a central global classifier so instances can talk to each other.
  **2026+**: each institution or domain runs its own personalised agent in a
  cooperative network; "Eesti keelele kohandatud SKM-i loomine". Knowledge
  ingestion is retrieval over institution-supplied source documents, no
  model training; hosted on Riigipilv (~€150/month + LLM cost); id.ee was
  the first SKM deployment. https://www.kratid.ee/burokratt ;
  https://www.ria.ee/en/state-information-system/artificial-intelligence/burokratt
- **AI action plan 2024–2026** (MKM): "Üldteadmusmudeli (GPT) liidestamine
  Bürokratiga", RIA, Q2 2024, budget €5.8M; target 100 institutions using
  Bürokratt by Q4 2026 (baseline 8); a Q2 2024 analysis on Bürokratt for
  low-capacity agencies and KOVs; EKI measures for a 15-billion-word
  Estonian dataset and an Estonian-culture-specific LLM (RRF / EKT funding).
  https://www.mkm.ee/media/10157/download
- **No MCP/A2A in official RIA material** (searched 2026-09-03). An
  unofficial "service hub" blueprint (gist, Jan 2026) proposes FIPA-ACL,
  X-Road as data fabric and a Rules-as-Code engine; not RIA policy.
  https://gist.github.com/ottomattas/7921aafbefe02ae2e05003429b758c4c
- **Aruait** (RIA innovation project, €1M, 24 months, launch event June
  2026): "Identity 2.0" for machine agents, a public trust registry of AI
  assistants, interoperability specs, a working pilot.
  https://www.ria.ee/riigi-infosusteem/tehisaru/aruait. Related: TARK trust
  assessments for cloud AI tools, Agent Registry pilot, AI sandbox
  (Ilves, Apr 2026):
  https://luukasilves.substack.com/p/building-the-agentic-state-in-estonia
- **State/Estonian LLM**: *EstLLM* (TartuNLP + EKI + TalTech; arXiv
  2603.02041, Mar 2026, revised Aug 2026) continued-pretrains Llama-3.1-8B and
  Apertus-8B; prototype `tartuNLP/llama-estllm-prototype-0825` (Aug 2025),
  funded by the EKT programme 2018–2027; training data lists the Estonian
  National Corpus etc., **no Riigi Teataja or legal data mentioned**.
  https://huggingface.co/tartuNLP/llama-estllm-prototype-0825 ;
  https://arxiv.org/abs/2603.02041
- **Digiühiskonna programm 2026–2029** (draft 1 Oct 2025, lead JDM): ensure
  Estonian in at least five major LLM training sets; continue the "digiriigi
  virtuaalne assistent"; create an AI competence centre and a technical +
  regulatory sandbox; public-sector AI use cases 178 → 380.
  https://www.fin.ee/sites/default/files/documents/2025-10/Digi%C3%BChiskonna%20programm%202026-2029.pdf
- **Eesti.ai** (PM-led, early 2026): €10.98M in 2026; 15 projects incl. EKI
  "Eesti keel ja kultuur suurtes keelemudelites" (public 10k-example
  dataset). https://eesti.ai/projektid ;
  https://www.riigikantselei.ee/en/supporting-government-and-prime-minister/eestiai-initiative

**Implication for this project.** Bürokratt does not consume MCP today; it
consumes institution-defined source documents for RAG. The lowest-friction
integration is therefore the retrieval JSONL projection (flattened
provision + version + act text) offered as a Bürokratt source, piloted with
one institution that already runs Bürokratt (e.g. Andmekaitse
Inspektsioon). The MCP endpoint fits the 2026 "agent network" direction and
Aruait's registry, so registering estleg-mcp as a trusted public AI
service is the medium-term path. Separately, EstLLM has no legal data: a
legal QA/evaluation set derived from provisions and Riigikohus links is a
concrete contribution.

---

## 5. X-tee and the open-data portal (andmed.eesti.ee)

- **The portal moved**: `avaandmed.eesti.ee` redirects to
  **https://andmed.eesti.ee/** ("Eesti riigi andmete portaal" / Teabevärav),
  operated by RIA under JDM (verified redirect 2026-09-03). Contacts:
  andmed@justdigi.ee, help at https://abi.ria.ee/teabevarav/.
  https://www.ria.ee/en/state-information-system/data-based-governance-and-reuse-data/estonian-data-portal
- **Non-government publishers are allowed**: "Avaandmete loojaks ja
  avaldajaks on enamasti avaliku sektori asutused, aga võivad olla ka
  ettevõtted, teadusasutused ja muud organisatsioonid" (kratid.ee); RIA's
  page says the portal accepts public bodies, businesses, research
  institutions and other organisations and **forwards every dataset
  description to data.europa.eu**. https://www.kratid.ee/avaandmed.
  The account-creation procedure is not documented publicly (the portal is
  a JS app); machine push exists via the "Mirroring Jobs API v2" with
  organisation authentication, test access via klient@ria.ee; state bodies
  import from RIHA. https://abi.ria.ee/teabevarav/liidestumine-atvga
- **Metadata profile**: *Andmekirjelduse standard* v3.0.1 (April 2026),
  built to be compatible with **DCAT-AP 3.0.0**; mandatory dataset fields:
  title, description, publisher, theme (EuroVoc or Estonian thesaurus),
  contact point, modification date, at least one distribution with format
  and byte size, access rights; licence at dataset and/or distribution
  level. No separately named "DCAT-AP-EE" profile exists; this standard is
  the Estonian profile. https://e-gov.github.io/Andmekirjelduse-Standard/stable/
- **Licence expectations**: RIA's publishing guide recommends **CC0** ("võiks
  andmete avaldamise kohtades viidata näiteks CC0 litsentsile"); the
  licensing guide lists the CC family; the portal lets the publisher choose.
  https://abi.ria.ee/teabevarav/avaandmete-loomise-ja-avaldamise-juhend ;
  https://abi.ria.ee/teabevarav/avaandmete-litsentsimise-luhikokkuvote
- **X-tee**: member classes GOV / COM / NGO; join by registering in RIHA,
  signing the membership agreement, running a security server, describing
  services (OpenAPI 3 or WSDL), granting access per subsystem, and
  concluding a data-service agreement with each consumer; joining is free
  but the member bears its own infrastructure cost.
  https://abi.ria.ee/xtee/andmeteenuse-tarbimine-ja-voi-pakkumine ;
  https://abi.ria.ee/xtee/x-teega-liitumine-ja-parimad-praktikad ;
  regulation https://www.riigiteataja.ee/akt/127092016004?leiaKehtiv

**Implication for this project.** Listing on andmed.eesti.ee is open to a
non-government publisher and is the cheapest official visibility (it also
lands on data.europa.eu). The existing `metadata.jsonld` with
`dcat:downloadURL` needs the standard's mandatory fields (theme, contact,
byte size per distribution, access rights) and an explicit licence URI;
CC BY 4.0 is defensible, CC0 is what RIA recommends, and the CC BY-SA 3.0
Riigikogu inputs must be accounted for. X-tee only pays off once a public
body wants to consume estleg through a security server; keep HTTP/MCP as
the primary transport until then.

---

## 6. Municipalities (KOV): supervision, pain points, adopters

- **Who supervises KOV regulations**: the Õiguskantsler exercises abstract
  norm control over KOV määrused (e.g. petition to Riigikohus, 4 June 2025,
  to void § 18 of Tallinna Linnavolikogu määrus nr 24 of 15.12.2022 after the
  city failed to amend it as promised in Nov 2024).
  https://www.oiguskantsler.ee/sites/default/files/2025-06/Taotlus%20Tallinna%20Linnavolikogu%2015.12.2022%20maaruse%20nr%2024%20Avalikult%20kasutatava%20ehitise%20ehitamise%20ja%20selle%20rahastamise%20kord%20SS%2018.pdf ;
  2023/24 review chapter: https://www.oiguskantsler.ee/ylevaade2024/linnad-ja-vallad ;
  2024/25 review states the principle: "määrus saab olla põhiseadusega
  kooskõlas üksnes juhul, kui see on antud ... seaduse normi (volitusnormi)
  alusel" https://www.oiguskantsler.ee/ylevaade2025/aastaulevaade.pdf
- **Ministry side**: haldusjärelevalve over KOV administrative acts sat with
  Justiitsministeerium (2021: 38 requests, 11 supervisions; 2022: 20+2) and
  advisory practice with Rahandusministeerium, per the 2023 overview
  (https://www.fin.ee/media/9932/download). Since 1 July 2023 the KOV
  department belongs to **Regionaal- ja Põllumajandusministeerium** (REM);
  the latest "Kohalike omavalitsuste 2021–2023. a järelevalve- ja
  nõustamispraktika ülevaade" (Dec 2024) is on agri.ee but the PDF is behind
  an anti-bot page, so its content is **unconfirmed**.
  https://www.agri.ee/sites/default/files/documents/2024-12/Kohalike%20omavalitsuste%202021%E2%80%932023.%20a%20j%C3%A4relevalve-%20ja%20n%C3%B5ustamispraktika%20%C3%BClevaade.pdf ;
  REM statute https://www.riigiteataja.ee/akt/128052025009
- **Documented pain points** (2023 overview): KOKS § 7 lg 4 applies HÕNTE to
  KOV regulation drafts "erisustega", but practice varies and many KOVs
  write no seletuskiri; confusion between a competence norm (KOKS § 6 lg 1)
  and a *volitusnorm*; praeter legem regulations are allowed for local
  matters (HMS § 90 lg 2) but any fundamental-rights restriction needs a
  statutory *volitusnorm*. **No official statistic on KOV regulations citing
  repealed enabling acts was found**; the state-level revision did note
  regulations waiting on *volitusnorm* changes (see § 2).
- **KOV IT**: there is no "KOV IT keskus" agency. The Eesti Linnade ja
  Valdade Liit (ELVL) IKT kompetentsikeskus (5 staff, 77 member KOVs) runs
  shared systems and projects: VOLIS (GPL), the new KOV rahvaküsitluste
  keskkond (Datel, due end-July 2026), Omavalitsuste Portaal pilot with five
  KOVs, "KOV teenuste platvorm" pre-analysis 2025–2026, in cooperation with
  REM. None of these handle legal acts; KOV regulations are published in
  RT's KOV section. https://www.elvl.ee/kov-it-koordineerimine ;
  https://elvl.ee/tegevussuunad-ja-valdkonnad/ikt-koordineerimine/uldinfo-ja-projektid
- The AI action plan foresaw a Q2 2024 analysis of Bürokratt for low-capacity
  agencies and KOVs (https://www.mkm.ee/media/10157/download).

**Implication for this project.** The adopter triangle is ELVL's IKT
centre (distribution to 77 KOVs), REM's KOV department (advisory practice)
and the Õiguskantsler's norm-control unit (enforcement). The artefact they
lack is a per-municipality legality view: each `MunicipalRegulation` →
cited enabling provision → that provision's status via the amendment
layer, plus HÕNTE-style completeness flags. Deliver it as CSV per KOV and
as SPARQL, and pilot with three to five KOVs through ELVL before offering
it to REM.

---

## 7. EU level: ELI, Interoperable Europe Act, EUR-Lex NTM, comparators

- **ELI status** (EUR-Lex table, 19 May 2025): Estonia pillar 1 only;
  Finland and Luxembourg all four pillars; Norway three plus partial sync;
  UK three plus. **ELI-DL 3.0.0** (draft legislation ontology) released
  20 Feb 2025. https://eur-lex.europa.eu/content/eli-register/implementation.html ;
  https://interoperable-europe.ec.europa.eu/collection/eli-european-legislation-identifier/solution/eli-ontology-draft-legislation-eli-dl
- **Interoperable Europe Act** (Reg. (EU) 2024/903, in force Apr 2024):
  since 12 Jan 2025 interoperability assessments are mandatory for new or
  significantly changed trans-European digital public services; Member
  States designate competent authorities; the Board adopted the first
  **Interoperable Europe Agenda on 4 Dec 2025**; first annual report
  16 Dec 2025; sandbox implementing act; the Commission commits to
  digital-ready legislation and runs a "Rules as Code & Digital Ready
  Legislation" strand. **No direct legal-data or ELI obligation** exists;
  ELI is a recommended SEMIC asset. Estonia's designated competent authority
  could not be confirmed (candidates: MKM digital-state department, JDM
  since 2025).
  https://eur-lex.europa.eu/eli/reg/2024/903/oj ;
  https://commission.europa.eu/news-and-media/news/state-interoperability-union-2025-12-16_en ;
  https://interoperable-europe.ec.europa.eu/interoperable-europe/news/two-years-interoperable-europe-act ;
  https://interoperable-europe.ec.europa.eu/collection/better-legislation-smoother-implementation/event/rules-code-digital-ready-legislation
- **EUR-Lex national transposition measures** are queryable in Cellar
  (verified 2026-09-03 against `https://publications.europa.eu/webapi/rdf/sparql`):
  NIMs are `cdm:measure_national_implementing` linked by
  `cdm:measure_national_implementing_implements_resource_legal` to the
  directive work (found via `cdm:resource_legal_id_celex`); properties
  include `..._implemented_by_country`, `..._name_official_journal`
  (Estonian entries read "Elektrooniline Riigi Teataja"), `..._reference_commission`
  (`MNE(2021)01334` style), `..._date_notification`, `cdm:work_title`
  (e.g. "Avaliku teabe seadus", "Ruumiandmete seadus", "Arhiiviseadus" for
  Directive 2019/1024; 214 NIMs total). EUR-Lex disclaimer: Member States
  are solely responsible for the content.
  https://eur-lex.europa.eu/collection/n-law/mne.html
- **Comparators a public body will benchmark against**:
  - Finland: Finlex open data REST API, Akoma Ntoso XML, no registration
    (https://www.finlex.fi/en/open-data); Semantic Finlex (Aalto SeCo, since
    2016) RDF + SPARQL with ELI/ECLI, next version planned
    (https://seco.cs.aalto.fi/projects/lawlod/en/).
  - Norway: Lovdata opened a free API on **3 Nov 2025** for laws and central
    regulations under NLOD 2.0, nightly packages, explicitly for AI/RAG use
    (https://api.lovdata.no/; https://lovdata.no/artikkel/.../5277);
    third-party MCP servers already wrap it
    (https://github.com/Ansvar-Systems/norwegian-law-mcp).
  - Netherlands: KOOP's Linked Data Overheid (LiDO) links laws, ECLI case law
    and EU acts; dump dated Aug 2026 on data.overheid.nl
    (https://data.overheid.nl/dataset/linked-data-overheid).
  - Luxembourg: data.legilux.public.lu SPARQL with JOLux + ELI models
    (https://data.legilux.public.lu/).
  - UK: legislation.gov.uk API since 2010 (XML/RDF/HTML fragments), LDRI
    research infrastructure (AHRC £550k, 2015), TNA's AIINFRA LLM project
    2024–2026 (https://www.legislation.gov.uk/projects/big-data-for-law;
    https://www.nationalarchives.gov.uk/.../our-current-projects/).
  - Austria: OGD-RIS API v2.6 (May 2024), daily machine-readable, history
    query (https://data.bka.gv.at/ris/ogd/v2.6/Documents/Dokumentation_OGD-RIS_API.pdf).

**Implication for this project.** Publish ELI-ontology metadata for acts
and ECLI for Riigikohus decisions, and align draft nodes with ELI-DL 3.0, so
that estleg is the Estonian analogue of Semantic Finlex or LiDO. Use the
Cellar NIM graph both to validate the existing EU transposition layer and
to import MNE references as `owl:sameAs`/provenance. Lovdata is the
argument to put in front of RT: an official publisher opening a free API
"tilpasset vår KI-hverdag".

---

## 8. Academic and funding homes

- **TartuNLP** (UT Institute of Computer Science, Mark Fišel) with EKI and
  TalTech (Tanel Alumäe) built EstLLM and EstBERT; the site tartunlp.ai is a
  JS app, models on https://huggingface.co/tartuNLP.
- **EKT programme "Eesti keeletehnoloogia 2018–2027"** (HTM via EKI):
  **two rounds open until 28 Sep 2026, 17:00** via ETIS: a targeted 2026
  round on new methods for evaluating generative AI, and an open 2027 round
  on base technologies and applications; eligible: public, private or
  third-sector organisations or consortia. Contact Käbi Laan, kabi.laan@eki.ee.
  https://eki.ee/uudis/eesti-keeletehnoloogia-programmi-uued-taotlusvoorud-on-avatud/
- **Tartu Ülikool õigusteaduskond**: IT-õigus (Information Technology Law)
  master's programme; 2025–2026 theses on AI and law; named as a HÕNTE
  stakeholder together with RAKE (UT applied social research). No dedicated
  legal-informatics or legal-NLP group was found.
  https://ut.ee/et/oppekavad/infotehnoloogiaoigus
- **TalTech Department of Law / Legal Lab**: research group on EU digital
  market and technology legal policy; Complymate GDPR tool; Prof. Tanel
  Kerikmäe died 1 Aug 2025. Ragnar Nurkse institute (Johanna Vallistu) spoke
  at the Nov 2025 õigusloojate konverents.
  https://taltech.ee/en/department-law-research-groups
- **CLARIN-EE**: Eesti Keeleressursside Keskus (UT/EKI/Kirmus consortium)
  hosts corpora and requires rights transfer to a partner before
  distribution; the only legal corpus found is the 2002 "Eesti keele
  segakorpus: Seadused" (legaltext.ee).
  https://keeleressursid.ee/et/eesti-keeleressursside-keskus ;
  https://www.cl.ut.ee/korpused/segakorpus/seadused/index.php?lang=et
- **Funding lines**: Eesti.ai €10.98M in 2026 (projects via ministries);
  ETAG RITA+ applied research commissioned by ministries
  (https://etag.ee/rahastamine/programmid/rita/...); Digital Europe
  DIGITAL-2026-BESTUSE-MCP-09 "Innovative and Connected Public
  Administrations" (€6M, closed 19 May 2026; next calls under the DEP
  2025–2027 work programme, DG DIGIT interoperability unit)
  (https://ec.europa.eu/info/funding-tenders/opportunities/docs/2021-2027/digital/wp-call/2026/call-fiche_digital-2026-bestuse-mcp-09_en.pdf);
  RRF-funded EKI LLM-data measures (AI action plan). JDM's "Teadustööd" page
  (updated 4 Mar 2026) lists only student prize competitions.

**Implication for this project.** Two funding-ready fits exist now: the EKT
2026 targeted round (a legal-domain evaluation benchmark for Estonian
generative AI derived from provisions, provision versions and Riigikohus
links; deadline 28 Sep 2026, consortium with TartuNLP/EKI and a law
faculty) and a RITA+ or Eesti.ai project-5 subcontract with JDM. A
CLARIN-EE deposit gives the corpus an academic home and a persistent
identifier, which also closes the open Zenodo DOI item.

---

## Top 8 concrete adoption pathways (ranked)

1. **JDM Eesti.ai project 5 "Õigusloomeprotsessi kvaliteet ja tõhusus"**
   — counterpart: JDM õigusloome korralduse osakond (Margit Juhkam) /
   Eesti.ai team. Artefact: conflict-and-gap substrate for a draft: for
   each provision cited or amended, `who_references`, `provision_history`,
   `transposition`, court links, plus retrieval JSONL for RAG. Format:
   estleg-mcp tools + JSON/REST, ELI identifiers. First step: run the
   tools against a live 2026 SE from api.riigikogu.ee and send a two-page
   demo to the project owner.
2. **Sätla / Riigi koosloome keskkond (go-live 1 Oct 2026)** — counterpart:
   project manager Karmen Vilms (JDM) with Riigikantselei and Riigikogu
   Kantselei. Artefact: a reference-resolution and provision-identifier
   service (act + § + lõige → estleg IRI → RT ELI URI), ELI-DL-aligned draft
   identifiers. Format: ELI/ELI-DL RDF, REST. First step: propose a
   read-only resolver pilot for the first-stage templates.
3. **Regulations revision / bureaucracy reduction (JDM)** — counterpart:
   JDM bürokraatia vähendamise team (ettepanekud form) and the ministries
   holding unfinished repeals. Artefact: list of regulations whose
   *volitusnorm* is repealed or amended, and KOV regulations citing them.
   Format: CSV + SPARQL queries. First step: compute from the amendment and
   cross-reference layers and submit via
   https://www.justdigi.ee/ettepanekud-burokraatia-vahendamiseks.
4. **RT ELI pillars 2–3 (JDM RT unit + RIK)** — artefact: ELI-ontology
   metadata for every act with `owl:sameAs` to RT ELI URIs, exemplar
   JSON-LD/RDFa, ECLI for Riigikohus. Format: ELI 1.x OWL, JSON-LD. First
   step: send the ELI mapping to the RT talitus and, in parallel, ask RIK for
   the public-api OpenAPI spec and rate policy.
5. **Bürokratt / RIA** — counterpart: buerokratt@ria.ee, one institution
   already on Bürokratt (Andmekaitse Inspektsioon). Artefact: retrieval
   JSONL as a RAG source (provision text as of date, with RT citations);
   later register estleg-mcp in the Aruait trust registry. Format: JSONL /
   HTTP MCP. First step: offer a one-institution pilot answering "what does
   the law require of me" with citations.
6. **KOV regulation legality view (ELVL IKT centre, REM KOV department,
   Õiguskantsler)** — artefact: per-municipality report of regulations →
   enabling provision → provision status, plus HÕNTE completeness flags.
   Format: CSV per KOV, SPARQL, later an ELVL portal widget. First step:
   pilot with three to five KOVs via ELVL.
7. **andmed.eesti.ee listing (RIA/JDM), harvested to data.europa.eu** —
   artefact: DCAT-AP 3.0 record per the Andmekirjelduse standard 3.0.1 with
   licence URI (CC BY 4.0 or CC0) and the GitHub Release distributions.
   Format: DCAT-AP JSON-LD/RDF. First step: request a non-government
   publisher account via klient@ria.ee / andmed@justdigi.ee.
8. **EKT 2026 targeted round (deadline 28 Sep 2026) and CLARIN-EE deposit**
   — counterpart: EKI programme office, TartuNLP, a law faculty. Artefact: an
   Estonian legal-reasoning evaluation set built from provisions, point-in-
   time versions and court→provision links; corpus deposit for a persistent
   identifier. Format: JSONL benchmark + CLARIN metadata. First step: draft
   the ETIS application and contact Käbi Laan (EKI).

## Things I could not confirm

- RT public-api rate limits or terms of use (none published).
- Content of REM's Dec 2024 KOV supervision overview (anti-bot page).
- Estonia's designated Interoperable Europe Act competent authority.
- Any official Estonian "Rules as Code" programme (none found).
- Whether Sätla will expose an API or ELI-DL data (law text not read).
