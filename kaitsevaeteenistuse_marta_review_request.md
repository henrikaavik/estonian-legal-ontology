# Marta review request: Kaitseväeteenistuse seadus map (KVTS)

Hi Marta,

I prepared initial JSON-LD mapping here:
- `krr_outputs/kaitsevaeteenistuse_peep.json`

Scope mapped:
1. Military service (`military_service`)
2. Reserve service (`reserve_service`)
3. Alternative service (`alternative_service`)

Please review:
- legal cluster boundaries
- term precision (`kaitseväeteenistus` vs `ajateenistus`)
- where exact § references should be attached in next pass

Current file is in unified schema and intentionally uses norm blocks (not exact section numbers yet) due source extraction limits from RT during this run.
