# KRR ettevalmistus: Tsiviilseadustiku analüüs (Marta jaoks)

Kuupäev: 2026-02-26
Vastutaja: Peep
Staatus: valmis (ootab Marta sisendit)

## 1) KRR infrastruktuuri ülevaade (praegune seis)

### Andmeallikad ja sisend
- `workspace-peep/data/riigiteataja/sample_law.xml` — Riigi Teataja seaduse XML näidis
- `workspace-peep/data/riigiteataja/sample_law_summary.json` — sama allika kokkuvõtte näidis JSON-is
- `workspace-peep/scripts/fetch_rt_sample.py` — skript RT näidiste toomiseks/ettevalmistuseks

### Töötluskiht (olemasolev + valmis kasutuseks)
- XML -> struktureeritud objektid (peatükk, jagu, paragrahv, lõige)
- JSON vahekiht analüüsi/annotatsiooni jaoks
- Võimalik siduda LLM-ekstraktsiooniga (norm, subjekt, kohustus, õigus, erand, tingimus, sanktsioon)

### Teadmuskiht (ette valmistatud mallid)
- Ontoloogia skeleton (klassid + seosed)
- Instantsitaseme triple-template (Turtle)
- Ekstraktsiooni JSON skeem kiireks poolautomaatseks täitmiseks

### Tööriistad ja asukohad
- Repositoorium: `/home/henrik-aavik/.openclaw/workspace-peep`
- Jagatud koostööfailid: `/home/henrik-aavik/.openclaw/shared/`
- Valmis ontoloogia mallifailid:
  - `/home/henrik-aavik/.openclaw/shared/krr_templates/civillaw_ontology_skeleton.ttl`
  - `/home/henrik-aavik/.openclaw/shared/krr_templates/civillaw_article_instance_template.ttl`
  - `/home/henrik-aavik/.openclaw/shared/krr_templates/civillaw_extraction_template.json`

## 2) Ontoloogia mallid (ette valmistatud)

### Minimaalne ontoloogia tuum
- Klassid: `LegalDocument`, `Article`, `Section`, `Norm`, `Subject`, `LegalAction`, `Condition`, `Exception`, `Sanction`, `Reference`
- Põhiseosed:
  - `hasArticle`, `hasSection`, `containsNorm`
  - `appliesToSubject`, `requiresAction`, `grantsRight`, `prohibitsAction`
  - `hasCondition`, `hasException`, `hasSanction`
  - `referencesProvision`
  - `effectiveFrom`, `effectiveUntil`

### Marta sisendi ootevorm
Marta saab kohe anda sisendi selles vormis:
1. Seaduse osa/peatüki piirid (mida analüüsime esimesena)
2. Kas fookus on kohustustel, õigustel või eranditel
3. Kas väljundiks on:
   - A) teadmugraafi täitmine (triples)
   - B) analüütiline memo
   - C) mõlemad

## 3) Järgmine samm (ooterežiim)
- KRR baas on ette valmistatud.
- Ootan Marta konkreetset sisendit (peatükid + analüüsi fookus).
