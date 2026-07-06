# What's new in this version

New since the previously published build of the q-eAON decision-support platform.

## Adverse-outcome network views (new)
- **Abstract AON panel.** Each chemical is wired through the IARC key
  characteristics it triggers, split into a variation-generating (initiation)
  arm and a selection-altering (promotion) arm, converging on shared nodes -
  so the *network*, not the single pathway, is what you see.
- **Molecular AOP cascade panel.** A second, more detailed view: molecular
  initiating event -> key events -> the shared tissue key event (clonal
  expansion) -> organ-specific outcome, laid out in sequential columns.
- Chemicals that share a step **converge** on the same node; one chemical
  altering another chemical's step is drawn as a **modifier edge**; edge
  strength reflects the confidence of the evidence.

## Live AOP-Wiki link - no key needed (new)
- A new `/aop` endpoint (`aop_client.py`) pulls ordered pathway chains straight
  from AOP-Wiki for a given chemical and maps the event titles onto a shared
  key-event vocabulary, so convergence between chemicals is automatic.
- About 40 chemicals are seeded across metals, nitrosamines, PAHs, pesticides,
  PFAS and aromatic amines. AOP-Wiki is a public resource, so **this needs no
  API key**.

## Save & load runs (new)
- Save a run (once data has been retrieved) to a named list in the browser, or
  export/import it as a file, and resume from that point later.

## Display & data fixes
- Organ **body-atlas heat map**.
- Fixed chemical-structure images (transparent backgrounds no longer show as a
  red box); larger rendering for big molecules (dioxins, PCBs, benzo[a]pyrene).
- Absolute (IRIS-anchored) risk is shown in scientific notation (e.g. 3.7e-5).
- Added the **1,4-dioxane** potency entry, which fixes a zero-risk result for
  chemicals that are silent in ToxCast; expanded key-characteristic and pathway
  coverage in the bundled data.

## No key or endpoint changes
- The EPA CompTox (`CTX_API_KEY`) and INTEGRA (`INTEGRA_API_KEY`) keys and URLs
  are unchanged. AOP-Wiki needs no key. If your app is already deployed, you do
  **not** re-enter any keys when you update the code.
