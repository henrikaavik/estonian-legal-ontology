Hi Marta,

I mapped **Notariaadiseadus** into the unified JSON-LD schema here:
- `krr_outputs/notari_seadus_peep.json`

Please review specifically:
1. Cluster fit for:
   - notari ülesanded
   - notariaalsed toimingud
   - notariaalne tõestamine
2. Whether we should add explicit **tõestamisseadus** nodes already in this file, or keep it as cross-reference only.
3. Any critical missing NotS anchors for the acts/authentication split.

Note: NotS §1(2) pushes authentication procedure into other laws, so this version models authentication as a linked framework rather than full procedural extraction.

Thanks!
