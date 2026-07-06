# Specification: translating `intensity` into real exposure / dose

Status: design spec (also intended as manuscript methods text). Implementation
follows the same tiered, source-cited discipline as the g/p connector.

## 1. What `intensity` means

`intensity` is redefined from a bare multiplier into a **normalised internal dose**:
the target-tissue dose a scenario produces, divided by a chemical-specific **point of
departure (POD)** — the internal concentration at which the carcinogenic-relevant
biology switches on.

```
intensity = D_internal(scenario) / POD_internal          (internal form)
          = ExternalDose(scenario) / AED_POD             (external form, equivalent)
```

- `intensity = 1`  → exposure that brings the target tissue to the POD concentration.
- `intensity = 0.1` → one tenth of the POD; `intensity = 10` → ten-fold into the bioactive range.

This is potency-adjusted (a potent carcinogen reaches intensity 1 at a far lower external
dose), so mixture components sit on a common biological axis — which is what the Shapley
apportionment needs.

## 2. The anchor from ToxCast (internal concentration)

Each active ToxCast/Tox21 assay reports an activity concentration (AC50, µM — half-maximal;
or ACC, the activity concentration at cutoff, µM — onset). Across a chemical's active
assays the most sensitive define its in-vitro POD. We compute **channel-specific PODs**,
because the two model channels respond to different biology:

```
POD_g  = 5th-percentile AC50 over active assays mapped to g-side KCs {1,2,3(,4,5)}
POD_p  = 5th-percentile AC50 over active assays mapped to p-side KCs {6,7,8,9,10(,4,5)}
```

(5th percentile follows EPA's ToxCast POD convention; `min` is the conservative variant.
Anchor choice — AC50 half-max vs ACC onset — is a documented switch; default AC50.)

Genotoxic carcinogens that ToxCast misses (benzene, formaldehyde) get their POD from the
authoritative tier instead — see §5.

## 3. The IVIVE bridge to external dose (httk via CTX)

An in-vitro µM is not a human exposure. EPA high-throughput toxicokinetics (httk) gives the
steady-state plasma concentration produced by a 1 mg/kg/day intake:

```
Css   (µM per mg/kg/day)                     [CTX Exposure domain: httk]
AED_POD (mg/kg/day) = POD_internal (µM) / Css
```

`AED_POD` is the administered equivalent dose at the POD. Both `Css` and EPA's predicted
human exposure (ExpoCast/SEEM, mg/kg/day) are retrievable from the CTX Exposure endpoints
with the same API key, so no external toxicokinetics package is required.

## 4. Dose–response: how intensity drives the two channels

The two channels take the normalised dose with **different shapes**, matching genotoxic vs
non-genotoxic mode of action:

```
x_g = D / POD_g ,   x_p = D / POD_p

Initiation (g channel, linear no-threshold):
    mu1(D) = mu0 * (1 + g * phi_g * x_g)

Selection (p channel, saturating / Hill, half-max at the POD):
    s(D)   = s0 + p * dSmax * x_p^h / (1 + x_p^h)      (h = 1 default; h > 1 = threshold-like)
```

- The g channel stays **linear with no safe threshold** (one-hit mutagenesis): below the POD
  the genotoxic contribution scales down proportionally, it does not vanish.
- The p channel **saturates** and is effectively **reversible** (selection returns toward s0
  when exposure stops) — the promoter biology.
- `phi_g`, `dSmax`, `h` are calibration constants with defaults chosen so the scheme
  **reduces to the current model** when h = 1 and intensity is used directly — i.e. this is a
  strict generalisation, backward compatible with today's runs.

## 5. POD hierarchy (which anchor is used)

Mirror the g/p tiering:

1. **Authoritative POD** — from EPA IRIS (RfC/RfD, oral slope factor) or an IARC/NTP
   quantitative POD, where it exists. Preferred for Tier-1 curated chemicals.
2. **ToxCast AC50 POD + httk IVIVE** — the §2–3 route, for data-rich chemicals.
3. **Read-across / QSAR POD** — for data-poor chemicals (flagged low confidence).

Each returned dose anchor is tagged with its tier and source, exactly like g/p.

## 6. Regulatory tie-in (free output)

Because `AED_POD` and predicted exposure are both available, the tool can report the
**Bioactivity-Exposure Ratio**:

```
BER = AED_POD / predicted_exposure ≈ 1 / intensity(real-world)
```

BER >> 1 (intensity << 1) is the accepted "ample margin" case; intensity >= 1 means exposure
has entered the bioactive range. This lands the model directly in NGRA language.

## 7. Worked example

Chemical with `POD_internal = 1 µM`, httk `Css = 0.5 µM per mg/kg/day`:
`AED_POD = 1 / 0.5 = 2 mg/kg/day`. A scenario exposure of `0.2 mg/kg/day` →
internal `0.1 µM` → `intensity = 0.2/2 = 0.1` (10% of the POD; BER = 10).
The app would show: *"intensity 1 = 2 mg/kg/day = 1 µM plasma; your scenario = 0.1."*

## 8. Implementation steps

1. **Probe** the two remaining CTX fields (see `probe_dose.py`): (a) where AC50/ACC lives
   for a chemical (top-level `ac50`/`acc` on a summary endpoint, or inside `mc5Param`);
   (b) the Exposure-domain endpoints for httk `Css` and SEEM predicted exposure.
2. **Connector**: compute `POD_g`, `POD_p`, `POD_overall` (µM) from active-assay AC50s;
   pull `Css` and predicted exposure; return a `dose` block:
   `{POD_g_uM, POD_p_uM, Css, AED_POD_mgkgday, predicted_exposure, BER, source, tier}`.
3. **Model/app**: redefine the stressor `intensity` field as `D/POD` with the §4 channel
   dose–response; display the anchor ("intensity 1 = X mg/kg/day = Y µM"); optionally let the
   user enter a real external dose or a biomarker and auto-fill intensity.
4. **Authoritative PODs** (Tier 1): add IRIS/POD values to the curated table for the
   chemicals where ToxCast can't supply a potency.

## 9. Key references

- Wetmore et al. 2015; Wambaugh et al. — httk / IVIVE reverse dosimetry (Css, AED).
- Paul Friedman et al. 2020 — ToxCast POD (5th-percentile AC50) vs traditional PODs.
- ExpoCast / SEEM (Ring et al. 2019) — predicted human exposure.
- Bioactivity-Exposure Ratio — NGRA margin-of-exposure framing.
