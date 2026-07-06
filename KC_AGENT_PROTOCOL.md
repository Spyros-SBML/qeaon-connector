# Tier-2 key-characteristics extraction agent — protocol

Use this when a chemical is **not** in the Tier-1 curated table and ToxCast coverage
is sparse (the case the connector flags as low/medium confidence). The agent drafts a
key-characteristics profile from authoritative sources, you confirm it, and it becomes
a permanent Tier-1 entry.

## Why it works this way

An LLM that *invents* a g/p is not defensible for regulatory use. So this agent is
constrained to **extract and cite**, never to assert from memory:

- every non-zero key characteristic must carry a citation (the validator rejects rows
  that don't — see `kc_agent.validate_row`);
- if no citable source is found for a characteristic, it scores 0 ("insufficient
  evidence"), not a guess;
- the output is a **DRAFT requiring expert confirmation**;
- the agent **feeds Tier 1** — it never runs live inside `/gp`, so the model's
  non-determinism never reaches a result silently. Once you append a reviewed row, that
  chemical is thereafter served deterministically from the curated table.

## How to run it

### Manual mode (no API key) — recommended

1. Print the grounded prompt:
   ```
   python kc_agent.py prompt "vinyl bromide"
   ```
2. Paste it into a grounded LLM session (e.g. Claude with web access). It returns a JSON
   row with KC weights + a citation for each non-zero KC.
3. **Review it** against the cited sources. Fix any weight you disagree with.
4. Save the reviewed JSON to a file and append it:
   ```
   python kc_agent.py append vinyl_bromide.json
   ```
   The chemical is now a Tier-1 entry — restart the connector and it auto-fills
   deterministically with `confidence: high`.

### Automated mode (optional, needs a key)

If you set `ANTHROPIC_API_KEY`, the agent can draft the row itself using web search:
```
python kc_agent.py draft "vinyl bromide" > vinyl_bromide.json
```
You still review before `append`. (The key is read from the environment, never stored.)

## The KC -> g/p mapping

g/p are **derived** from the 10 KC weights using the same partition as the rest of the
app, so Tier-1, Tier-2 and ToxCast all sit on one scale:

| KC | characteristic | g | p |
|----|----------------|---|---|
| 1 | electrophilic / metabolically activated | 1.0 | 0 |
| 2 | genotoxic | 1.0 | 0 |
| 3 | alters DNA repair / genomic instability | 1.0 | 0 |
| 4 | epigenetic | 0.5 | 0.5 |
| 5 | oxidative stress | 0.5 | 0.5 |
| 6 | chronic inflammation | 0 | 1.0 |
| 7 | immunosuppressive | 0 | 1.0 |
| 8 | receptor-mediated | 0 | 1.0 |
| 9 | immortalisation | 0 | 1.0 |
| 10 | proliferation / death / nutrient | 0 | 1.0 |

`g = min(1, Σ wᵢ·gᵢ / 3)`, `p = min(1, Σ wᵢ·pᵢ / 3)` (weights wᵢ: 1.0 strong, 0.5 some).
