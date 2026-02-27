# Tsiviilseadustiku analüüsi ülevaatus ja täpsustused
## Tambeti läbivaatus Peepi tööl

Kuupäev: 2026-02-26
Algallikas: tsiviilseadustik_structural_analysis_20p.json

---

## Üldised tähelepanekud

### 1. Normitüüpide täpsustamine
Praegune liigitus (other/right/obligation) on liiga üldine. Vaja täpsustada:

**Ettepanekud normitüüpideks:**
- `declarative` - deklaratiivne (mis on, kuidas defineeritakse)
- `definition` - mõiste määratlus
- `imperative` - käskiv/sundiv (peab, on kohustatud)
- `prohibitive` - keelav (ei tohi, on keelatud)
- `permissive` - lubav (võib, on õigus)
- `conditional_imperative` - tingimuslik käskiv
- `conditional_permissive` - tingimuslik lubav
- `sanction` - sanktsioon/tagajärg (tühi, keelustatud, karistatav)
- `procedural` - protseduuriline (kuidas midagi teha)
- `hierarchy` - ülemuslikkuse norm (tava vs seadus)

### 2. Subjekti täpsustamine
`subject_guess` vajab täpsustamist:
- `isik` → täpsustada: `füüsiline_isik`, `juriidiline_isik`, `pooled_koos`, `kolmas_isik`
- `kohus` → täpsustada: `kohus_üldine`, `halduskohus`, `riigikohus`
- `määramata` → püüda määrata või märkida kui `kontekstist_sõltuv`

### 3. Semantiliste väljade täiendamine
Lisaks condition_present/exception_present (jah/ei) vaja lisada:
- `condition_type`: ajaline, faktiline, subjektiivne, õiguslik
- `condition_semantics`: tingimuse sisu lühidalt
- `exception_type`: erandi liik
- `exception_semantics`: erandi sisu

---

## Detailne ülevaatus paragrahvide kaupa

### Paragrahv 1 - Seaduse ülesanne

**Praegune analüüs:**
```json
{
  "id": "1_1",
  "type": "other",
  "subject_guess": "määramata",
  "condition_present": "ei",
  "exception_present": "ei",
  "source": "Käesolevas seaduses sätestatakse tsiviilõiguse üldpõhimõtted."
}
```

**Täpsustus:**
```json
{
  "id": "1_1",
  "type": "declarative",
  "subject_guess": "seadusandja",
  "subject_semantics": "seadusandja määrab seaduse reguleerimisala",
  "condition_present": "ei",
  "exception_present": "ei",
  "confidence": "kõrge",
  "notes": "Seaduse preambulilaadne sissejuhatus, deklareerib seaduse eesmärgi"
}
```

---

### Paragrahv 2 - Tsiviilõiguse allikad

**Praegune analüüs (2_1):**
```json
{
  "id": "2_1",
  "type": "other",
  "subject_guess": "määramata",
  "condition_present": "ei",
  "exception_present": "ei",
  "source": "(1) Tsiviilõiguse allikad on seadus ja tava."
}
```

**Täpsustus:**
```json
{
  "id": "2_1",
  "type": "definition",
  "subject_guess": "õigussüsteem",
  "subject_semantics": "abstraktne õiguskord",
  "condition_present": "ei",
  "exception_present": "ei",
  "confidence": "kõrge",
  "notes": "Definitsioon - loetleb tsiviilõiguse allikad"
}
```

**Praegune analüüs (2_2):**
```json
{
  "id": "2_2",
  "type": "other",
  "subject_guess": "isik",
  "condition_present": "jah",
  "exception_present": "ei",
  "source": "(2) Tava tekib käitumisviisi pikemaajalisest rakendamisest, kui käibes osalevad isikud peavad seda õiguslikult siduvaks. Tava ei saa muuta seadust."
}
```

**Täpsustus:**
```json
{
  "id": "2_2",
  "type": "declarative",
  "subject_guess": "tava",
  "subject_semantics": "abstraktne õigusallikas (tava)",
  "action": "tekkimine",
  "action_semantics": "tava tekib aja jooksul praktikast",
  "condition_present": "jah",
  "condition_type": "faktiline_ja_subjektiivne",
  "condition_semantics": "käitumisviisi pikemaajaline rakendamine + osapooled peavad siduvaks",
  "exception_present": "ei",
  "hierarchy_note": "Tava ei saa muuta seadust (hierarhiline norm)",
  "confidence": "kõrge",
  "notes": "Kahest osast: 1) tava tekkimise tingimused, 2) tava allumine seadusele"
}
```

---

### Paragrahv 3 - Seaduse tõlgendamine

**Praegune analüüs:**
```json
{
  "id": "3_1",
  "type": "other",
  "subject_guess": "määramata",
  "condition_present": "ei",
  "exception_present": "ei",
  "source": "Seaduse sätet tõlgendatakse koos seaduse teiste sätetega, lähtudes seaduse sõnastusest, mõttest ja eesmärgist."
}
```

**Täpsustus:**
```json
{
  "id": "3_1",
  "type": "imperative",
  "subject_guess": "õiguse_mõistja",
  "subject_semantics": "kohus, jurist, õiguse rakendaja",
  "action": "tõlgendamine",
  "action_semantics": "seaduse sätete mõistmine ja rakendamine",
  "condition_present": "ei",
  "exception_present": "ei",
  "methodology": "süsteemtõlgendus (sõnastus + mõte + eesmärk)",
  "confidence": "kõrge",
  "notes": "Seadustõlgenduse meetodoloogiline norm - kohustuslikud tõlgenduspõhimõtted"
}
```

---

### Paragrahv 4 - Analoogia

**Praegune analüüs:**
```json
{
  "id": "4_1",
  "type": "other",
  "subject_guess": "määramata",
  "condition_present": "jah",
  "exception_present": "ei",
  "source": "Õigussuhet reguleeriva sätte puudumisel kohaldatakse sätet, mis reguleerib reguleerimata õigussuhtele lähedast õigussuhet, kui õigussuhte reguleerimata jätmine ei vasta seaduse mõttele ega eesmärgile. Sellise sätte puudumisel lähtutakse seaduse või õiguse üldisest mõttest."
}
```

**Täpsustus:**
```json
{
  "id": "4_1",
  "type": "conditional_permissive",
  "subject_guess": "õiguse_mõistja",
  "subject_semantics": "kohus, õiguse rakendaja",
  "action": "analoogia_kohaldamine",
  "action_semantics": "lähedase sätte kohaldamine puuduva sätte asemel",
  "condition_present": "jah",
  "condition_type": "õiguslik_ja_faktiline",
  "condition_semantics": "1) sätte puudumine, 2) lähedane õigussuhe olemasolu, 3) reguleerimata jätmise vastuolu seaduse mõtte/eesmärgiga",
  "sub_conditions": [
    "sätte_puudumine",
    "lahenduse_vajalikkus",
    "analoogiline_suhe_olemas"
  ],
  "fallback_rule": "Kui analoogiline sätet pole, siis üldise mõtte põhimõtted",
  "exception_present": "ei",
  "confidence": "keskmine",
  "notes": "Keeruline hierarhiline struktuur: esmase analoogia tingimused + varuvariant"
}
```

---

### Paragrahv 5 - Tsiviilõiguste ja -kohustuste tekkimise alused

**Praegune analüüs:**
```json
{
  "id": "5_1",
  "type": "other",
  "subject_guess": "kohus",
  "condition_present": "ei",
  "exception_present": "ei",
  "source": "Tsiviilõigused ja -kohustused tekivad tehingutest, seaduses sätestatud sündmustest ja muudest toimingutest, millega seadus seob tsiviilõiguste ja -kohustuste tekkimise, samuti õigusvastastest tegudest."
}
```

**Täpsustus:**
```json
{
  "id": "5_1",
  "type": "declarative",
  "subject_guess": "õigussuhe",
  "subject_semantics": "tsiviilõigused ja -kohustused (abstraktsed)",
  "action": "tekkimine",
  "action_semantics": "õiguste ja kohustuste tekkimine õigussuhtes",
  "condition_present": "jah",
  "condition_type": "faktiliste_asjaolude_loetelu",
  "condition_semantics": "4 alust: 1) tehingud, 2) seaduses sätestatud sündmused, 3) seadusega seotud toimingud, 4) õigusvastased teod",
  "exception_present": "ei",
  "source_categories": ["tehingud", "seaduslikud_sündmused", "seadusega_seotud_toimingud", "õigusvastased_tegud"],
  "confidence": "kõrge",
  "notes": "Õiguste/kohustuste tekkimise aluste loetelu (exhaustive)"
}
```

---

### Paragrahv 6 - Õigusjärglus

**Praegune analüüs (6_1):**
```json
{
  "id": "6_1",
  "type": "other",
  "subject_guess": "kohus",
  "condition_present": "jah",
  "exception_present": "ei",
  "source": "(1) Tsiviilõigused ja -kohustused võivad üle minna ühelt isikult teisele (õigusjärglus), kui need ei ole seadusest tulenevalt isikuga lahutamatult seotud."
}
```

**Täpsustus:**
```json
{
  "id": "6_1",
  "type": "conditional_permissive",
  "subject_guess": "tsiviilõigused_kohustused",
  "subject_semantics": "õigused ja kohustused objektina",
  "action": "üle_minek",
  "action_semantics": "õiguste/kohustuste üleminek ühelt isikult teisele",
  "condition_present": "jah",
  "condition_type": "õiguslik_negatiivne",
  "condition_semantics": "õigused/kohustused EI OLE isikuga lahutamatult seotud (seadusest tulenevalt)",
  "exception_present": "ei",
  "confidence": "kõrge",
  "notes": "Õigusjärgluse üldpõhimõte + piirang (lahutamatult seotud õigused)"
}
```

**Praegune analüüs (6_2):**
```json
{
  "id": "6_2",
  "type": "other",
  "subject_guess": "määramata",
  "condition_present": "ei",
  "exception_present": "ei",
  "source": "(2) Õigusjärgluse aluseks on tehing või seadus."
}
```

**Täpsustus:**
```json
{
  "id": "6_2",
  "type": "declarative",
  "subject_guess": "õigusjärglus",
  "subject_semantics": "õigusjärgluse faktiline/aluseline sisu",
  "action": null,
  "condition_present": "ei",
  "exception_present": "ei",
  "bases": ["tehing", "seadus"],
  "confidence": "kõrge",
  "notes": "Õigusjärgluse aluste loetelu (kaheetapiline)"
}
```

**Praegune analüüs (6_3):**
```json
{
  "id": "6_3",
  "type": "obligation",
  "subject_guess": "kohus",
  "condition_present": "jah",
  "exception_present": "ei",
  "source": "(3) Õigused ja kohustused antakse üle üleandmise tehinguga (käsutustehing). Iga õigus ja kohustus tuleb eraldi üle anda, kui seadusest ei tulene teisiti."
}
```

**Täpsustus:**
```json
{
  "id": "6_3",
  "type": "imperative",
  "subject_guess": "üleandja",
  "subject_semantics": "õiguste/kohustuste üleandja (tehingu pool)",
  "action": "üleandmine",
  "action_semantics": "õiguste/kohustuste üleandmine käsutustehinguga",
  "condition_present": "jah",
  "condition_type": "protseduuriline",
  "condition_semantics": "käsutustehingu vorm + eraldi üleandmise nõue (välja arvatud seaduse erandid)",
  "exception_present": "jah",
  "exception_type": "seaduse_erand",
  "exception_semantics": "seadus võib sätestada teisiti (kumulatiivne üleandmine lubatud)",
  "confidence": "kõrge",
  "notes": "Käsutustehingu imperatiiv + erand kumulatiivseks üleandmiseks"
}
```

---

### Paragrahv 7 - Füüsilise isiku õigusvõime

**Praegune analüüs (7_1):**
```json
{
  "id": "7_1",
  "type": "other",
  "subject_guess": "kohus",
  "condition_present": "ei",
  "exception_present": "ei",
  "source": "(1) Füüsilise isiku (inimese) õigusvõime on võime omada tsiviilõigusi ja kanda tsiviilkohustusi. Igal füüsilisel isikul on ühetaoline ja piiramatu õigusvõime."
}
```

**Täpsustus:**
```json
{
  "id": "7_1",
  "type": "definition",
  "subject_guess": "füüsiline_isik",
  "subject_semantics": "iga inimene (sünnist surmani)",
  "action": "omamine_kandmine",
  "action_semantics": "õiguste omamine ja kohustuste kandmine",
  "condition_present": "ei",
  "exception_present": "ei",
  "key_attributes": {
    "ühetaoline": "võrdne kõigile",
    "piiramatu": "ei ole piiranguid (absoluutne)"
  },
  "confidence": "kõrge",
  "notes": "Õigusvõime definitsioon + selle universaalsuse printsiip"
}
```

**Praegune analüüs (7_2):**
```json
{
  "id": "7_2",
  "type": "other",
  "subject_guess": "määramata",
  "condition_present": "ei",
  "exception_present": "ei",
  "source": "(2) Õigusvõime algab inimese elusalt sündimisega ja lõpeb surmaga."
}
```

**Täpsustus:**
```json
{
  "id": "7_2",
  "type": "declarative",
  "subject_guess": "õigusvõime",
  "subject_semantics": "õigusvõime olemasolu kestvus",
  "action": "kestmine",
  "action_semantics": "õigusvõime olemasolu eluajal",
  "condition_present": "jah",
  "condition_type": "ajaline",
  "condition_semantics": "algus: elusalt sündimine, lõpp: surm",
  "exception_present": "ei",
  "confidence": "kõrge",
  "notes": "Õigusvõime ajaline kestvus (periood)"
}
```

**Praegune analüüs (7_3):**
```json
{
  "id": "7_3",
  "type": "other",
  "subject_guess": "määramata",
  "condition_present": "jah",
  "exception_present": "ei",
  "source": "(3) Seaduses sätestatud juhtudel on inimloode õigusvõimeline alates eostamisest, kui laps sünnib elusana."
}
```

**Täpsustus:**
```json
{
  "id": "7_3",
  "type": "conditional_declarative",
  "subject_guess": "inimloode",
  "subject_semantics": "sündimata laps (alates eostamisest)",
  "action": "õigusvõime_olemasolu",
  "action_semantics": "eelseisev õigusvõime lootel",
  "condition_present": "jah",
  "condition_type": "kombineeritud_seaduslik_ja_faktiline",
  "condition_semantics": "1) seaduses sätestatud juhtum, 2) eostamine toimunud, 3) laps sünnib elusana (tingimus resolutsioon)",
  "suspensive_condition": "seaduse sätted lootel",
  "resolutive_condition": "laps peab sündima elusana",
  "exception_present": "ei",
  "confidence": "kõrge",
  "notes": "Eriotstarbeline õigusvõime lootel - kahepoolne tingimuslikkus"
}
```

---

## Järgmiste paragrahvide ülevaatus (kokkuvõte)

### Paragrahv 8 - Füüsilise isiku teovõime
- **Probleem:** subject_guess="isik" on liiga üldine
- **Täpsustus:** vaja eristada `täisealine`, `alaealine`, `piiratud_teovõimega_isik`
- **Lisamärkus:** Teovõime piirangud sisaldavad meditsiinilisi tingimusi (vaimuhaigus, nõrgamõistuslikkus) - need on faktilised tingimused

### Paragrahv 9 - Vähemalt 15-aastase alaealise piiratud teovõime laiendamine  
- **Probleem:** type="right" on täpne, aga subject_guess="kohus" vajab konteksti
- **Täpsustus:** See on `kohus` + `protseduuriliste_tingimustega` (alaealise huvid, arengutase)
- **Lisamärkus:** 9_2 sisaldab erandit (seadusliku esindaja nõusolekuta laiendamine)

### Paragrahv 10-11 - Piiratud teovõimega isiku tehingud
- **Probleem:** Normide komplekssus (ühepoolne vs mitmepoolne)
- **Täpsustus:** 10_1 on `sanction` (tühine), 11_1 on `conditional_sanction` (tühine, välja arvatud...)
- **Lisamärkus:** Heakskiitmise mehhanism (ratifitseerimine) on eraldi protseduuriline norm

### Paragrahv 12 - Alla 7-aastase alaealise tehing
- **Probleem:** Absoluutne keeld vs erandid
- **Täpsustus:** 12_1 on `sanction` (absoluutne), 12_2 on `conditional_sanction_with_exception`
- **Lisamärkus:** Erand (12_2 lõige 2) sisaldab täpset tingimust - vahendite saamine

### Paragrahv 13 - Otsusevõimetu isiku tehing
- **Probleem:** Ajutine vs kestev otsusevõimetus
- **Täpsustus:** Ajutine seisund (vaimutegevuse häire) + heakskiidu mehhanism
- **Lisamärkus:** Presumptsioon (13_3) - kahjuliku tehingu eeldus

### Paragrahv 14-16 - Elukoht ja tegevuskoht
- **Probleem:** Definitsioonid + õiguslikud tagajärjed
- **Täpsustus:** Need on `definition` + `declarative` kombinatsioonid
- **Lisamärkus:** 14_3 sisaldab muutumise kriteeriume (tahte järeldamine)

### Paragrahv 17-20 - Teadmata kadunud isik
- **Probleem:** Definitsioon + protseduur + sanktsioonid
- **Täpsustus:** 17 on `definition`, 18 on `procedural_rights_obligations`, 19-20 on `procedural_conditions`
- **Lisamärkus:** Ajaperioodid on kategoorilised (5 aastat, 6 kuud, 2 aastat)

---

## Üldised soovitused

### 1. Normitüüpide täpsustamine
Lisada `norm_classification` väli, mis sisaldab:
- `primary_type`: declarative, imperative, permissive, prohibitive, sanction
- `secondary_type`: conditional, procedural, definitional, hierarchical
- `composite`: true/false (mitme tüübi kombinatsioon)

### 2. Subjekti semantika
Lisada `subject_hierarchy`:
- `primary_subject`: peamine subjekt
- `secondary_subjects`: kaasatud subjektid
- `beneficiary`: kasusaaja (kui erineb subjektist)
- `affected_party`: mõjutatud pool

### 3. Tingimuste struktuur
Muuta `condition_present` (jah/ei) -> `conditions` (massiiv):
```json
"conditions": [
  {
    "type": "faktiline|subjektiivne|õiguslik|ajaline",
    "description": "tingimuse sisu",
    "temporal_aspect": "enne|ajal|pärast|kestel",
    "strictness": "absoluutne|eeldatav|kaalutlusõiguslik"
  }
]
```

### 4. Erandite struktuur
Samamoodi `exceptions` massiiv:
```json
"exceptions": [
  {
    "type": "seaduse_erand|faktiline_erand|subjektiivne_erand",
    "description": "erandi sisu",
    "trigger": "millal erand rakendub"
  }
]
```

### 5. Ebakindlused ja märkused
Lisada iga normi juurde:
```json
"confidence": "kõrge|keskmine|madal",
"uncertainty_reason": "kui confidence != kõrge",
"review_needed": true/false,
"notes": "täpsustused ja tähelepanekud"
```

---

## Kokkuvõte

Peepi loodud struktuur on hea alus. Peamised parandused:

1. **Normitüüpide täpsustamine** - "other" asendamine semantiliselt täpse klassifikatsiooniga
2. **Subjekti täpsustamine** - "isik" ja "määramata" täpsustamine kontekstis
3. **Tingimuste struktuuri rikastamine** - jah/ei asemel detailne semantika
4. **Erandite struktuuri rikastamine** - sama lähenemine
5. **Usaldusväärse märkimine** - ebakindlate kohtade tähistamine

Soovitan edasi liikuda ühe paragrahvi kaupa, rakendades neid täpsustusi, ja testida JSON struktuuri valideerimisega.
