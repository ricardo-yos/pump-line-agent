# Example runs

One example per file in `scenarios/`, showing the kind of judgment the
agent is meant to produce. These are real transcripts from
`run_scenarios.py`, captured from the current project state; they are
examples, not a claim that every run will produce exactly the same
wording or tool-call sequence.

The examples are included to make the agent's behavior concrete:
how it weighs engineering trade-offs, identifies missing information,
uses deterministic tools, and avoids making unsupported assumptions.

---

## 01 — pump-only, weighing flow match against power

**Task**

> Select a pump for Q=20 m3/h and H=25 m.

**Response**

**Recommended pump**

* **Model ID:** `050-032-125_d120mm_3500rpm`
* **Real operating point:** Q ≈ 23.9 m³/h (≈ +20% vs. the 20 m³/h target) at the required 25 m head
* **Power consumption:** ≈ 2.45 kW

**Why this pump is preferred**

| Model ID                     | Q real (m³/h) | % deviation from 20 m³/h | Power (kW) | Reason for selection                                                      |
| ---------------------------- | ------------- | ------------------------ | ---------- | ------------------------------------------------------------------------- |
| 050-032-125_d120mm_3500rpm   | 23.9          | +20%                     | 2.45       | Meets head, lowest power of all passing pumps; deviation is modest.       |
| 050-032-160.1_d150mm_3500rpm | 21.6          | +8%                      | 4.20       | Closer flow match but power ≈ 70% higher, making it less economical.      |
| 050-032-125.1_d134mm_3500rpm | 24.1          | +20%                     | 2.67       | Slightly higher power than the chosen pump with virtually the same flow.  |
| 050-032-125_d130mm_3500rpm   | 36.6          | +83%                     | 3.78       | Overshoots flow considerably; unnecessary power.                          |
| 050-032-125.1_d139mm_3500rpm | 26.6          | +33%                     | 3.04       | Higher power and larger deviation.                                        |
| 050-032-160_d132mm_3500rpm   | 28.9          | +44%                     | 3.28       | Same trend as above.                                                      |
| 065-040-125_d120mm_3500rpm   | 30.4          | +52%                     | 2.95       | Power higher while flow far above target.                                 |
| … (remaining candidates)     | –             | –                        | –          | All either far exceed the target flow or consume considerably more power. |

**Rejected candidates (summary from the tool)**

* **Insufficient head** – 7 models whose curve at 20 m³/h delivered less than the required 25 m head (e.g., “H(20)=17.64 m < required 25 m”).
* **No operating point in range** – 11 models whose curves never intersect the 25 m head line within the catalogued flow range, so they cannot operate at the required head.

**Additional notes**

No NPSH data were supplied, and the pump-only mode does not require
an NPSH check.

All listed pumps satisfy the head requirement; the chosen unit balances
a modest flow overshoot with the lowest motor power, giving the most
economical operation for the specified 20 m³/h / 25 m system.

**What this shows:** `select_pump` returns passing candidates without
automatically applying a fixed "best pump" rule. The agent has to weigh
flow deviation against power and explain why the chosen candidate is a
reasonable engineering compromise.

---

## 02 — pipe + pump, no suction elevation given

**Task**

> Two reservoirs, static head 15m, target flow 20 m3/h. Suction pipe is 10m long, discharge pipe is 90m long. Recommend the pipe diameters and pump.

**Response**

I can compute the pipe-diameter combinations and find pumps that meet
the system curve, but I need the suction-side elevation to evaluate
NPSH margin.

**What’s missing:** the static head on the suction side
(`suction_static_head_m`) – i.e., the elevation of the liquid surface
in the source reservoir relative to the pump centreline (positive if
the source is above the pump, negative if the pump must lift the
liquid).

Please provide that value (or confirm that it can be treated as
zero/flooded suction), and I’ll run the pipe-pump grid and give you the
recommended pipe sizes and pump model.

**What this shows:** the agent identifies that the pipe lengths alone
are not enough to evaluate NPSH and asks for the missing suction
elevation instead of inventing it.

---

## 03 — pipe + pump with flooded suction

**Task**

> Transfer water between two reservoirs, static head 15m, flow 20 m3/h. Suction line is 10m long, discharge line is 90m. The source reservoir's water level sits 2m above the pump centerline. Size the pipes and select a pump, checking NPSH.

**Response**

**Recommended pipe-pump package**

| Item                       | Size / ID | Internal Ø (mm) | Velocity (m/s) |
| -------------------------- | --------- | --------------- | -------------- |
| Suction pipe (PEAD PN10)   | **de90**  | 79.2 mm         | 1.13 m/s       |
| Discharge pipe (PEAD PN10) | **de75**  | 66.0 mm         | 1.62 m/s       |

**Pump**

*Model ID*: **065-040-125_d104mm_3500rpm** (KSB Etanorm series)

*Operating point* (the real intersection of the pump curve with the
system curve for the chosen pipe pair)

* Q_real = 19.73 m³/h (−1.37% vs. target 20 m³/h)
* H_real = 18.34 m
* P_real = 1.69 kW
* NPSHr = 1.78 m
* NPSHa = 11.938 m
* NPSH margin ≈ 10.16 m, well above the required safety (≥ 0.5 m)

### Why this combination wins

| Suction      | Discharge    | System head @ 20 m³/h (m) | NPSHa (m)  | Pumps passing | Best pump (power)                          | Q-deviation % |
| ------------ | ------------ | ------------------------- | ---------- | ------------- | ------------------------------------------ | ------------- |
| de90 (79 mm) | de75 (66 mm) | **18.43**                 | **11.938** | 22            | **065-040-125_d104mm_3500rpm** – 1.69 kW   | **1.37**      |
| de90 (79 mm) | de90 (79 mm) | 16.52                     | **11.938** | 16            | **050-032-125.1_d114mm_3500rpm** – 1.54 kW | 2.95          |
| de75 (66 mm) | de75 (66 mm) | 18.64                     | 11.73      | 23            | 065-040-125_d104mm_3500rpm – 1.67 kW       | 3.87          |
| de75 (66 mm) | de63 (55 mm) | 22.96                     | 11.73      | 28            | 065-050-125_d112mm_3500rpm – 2.72 kW       | 2.74          |
| de90 (79 mm) | de63 (55 mm) | 22.75                     | 11.938     | 28            | 065-050-125_d112mm_3500rpm – 2.74 kW       | 1.33          |
| de63 (55 mm) | de63 (55 mm) | 23.44                     | 11.25      | 28            | 050-032-125.1_d124mm_3500rpm – 1.93 kW     | 4.90          |

* **Power vs. flow match** – The de90|de75 pair gives the lowest motor power among candidates that keep the delivered flow within ±2% of the target. The de90|de90 option is slightly cheaper (1.54 kW) but its flow is 2.95% low, a somewhat larger deviation for only a 0.15 kW saving.
* **NPSH margin** – All candidates that passed the NPSH gate have a large margin. The de75|de63 and de90|de63 combos show NPSH-margin rejections because a smaller suction pipe increases suction-friction loss, reducing NPSHa for some pumps.
* **System head** – The de90|de75 system head (≈18.4 m) is close to the static head (15 m) plus friction losses, keeping the required pump head relatively low.
* **Velocity limits** – Both selected pipes stay inside the prescribed 1.0–2.5 m/s range.

### Why the other candidates were rejected

* **NPSH-margin failures** – Some candidates using smaller suction pipes have NPSH margins below the required 0.5 m.
* **Head-excess or curve-miss** – Several pumps never intersect the system curve within their digitized range.
* **Large flow deviation** – Some low-power pumps pass the NPSH check but deliver substantially less than the target flow, while some larger pumps deliver excessive flow.

### Summary

* **Pipe sizes:** suction = PEAD PN10 de90 (79.2 mm ID), discharge = PEAD PN10 de75 (66.0 mm ID).
* **Pump:** KSB Etanorm model `065-040-125_d104mm_3500rpm`.
* **Performance:** 19.73 m³/h flow, 18.34 m head, 1.69 kW input, NPSH margin ≈ 10.16 m.

This combination meets the required flow and head, provides ample NPSH
margin, respects the velocity limits, and does so with the lowest
realistic motor power while keeping the flow deviation well under 2%.

*Editorial note:* the original model response contained three
cross-row transcription errors. The NPSHa for the selected de90|de75
combination was incorrectly copied as 11.73 m instead of 11.938 m,
and the same value was incorrectly used for that row in the comparison
table. The de90|de90 row also inherited the wrong NPSHa and incorrectly
named the pump as `065-040-125_d104mm_3500rpm`; the corresponding JSON
row identifies the pump as `050-032-125.1_d114mm_3500rpm` with the
reported 1.54 kW power. The underlying grid calculations were correct;
these are transcription/synthesis errors in the LLM response.

---

## 04 — pipe + pump with suction lift

**Task**

> Same system: static head 10m, flow 10 m3/h, suction 5m, discharge 30m -- but the pump sits 2m above the source reservoir's water level. Check whether NPSH is still acceptable.

**Response**

**Result – NPSH is fine**

Using the supplied data

* Q = 10 m³/h
* static-head = 10 m
* suction-pipe L = 5 m
* discharge-pipe L = 30 m
* pump sits 2 m above the source surface → suction_static_head = −2 m

`compute_pipe_pump_grid` gave three viable suction/discharge pipe
size combinations.

The smallest-diameter combination (both pipes **PEAD-PN10 DE50**,
internal Ø 44 mm) yields:

| Item                                          | Value                                            |
| --------------------------------------------- | ------------------------------------------------ |
| System head at Qtarget (10 m³/h)              | 12.57 m                                          |
| NPSH available (NPSHa)                        | **7.72 m**                                       |
| NPSH margin (NPSHa − NPSHr) for the best pump | **5.98 m**                                       |
| Passed pumps (total)                          | 24 (4 rejected because their NPSHr was too high) |

A 5.98 m margin is well above the 0.5 m threshold used by the agent,
so NPSH is acceptable for the selected combination.

**Best pump-pipe combination**

| Pump model (catalog ID)          | Suction pipe           | Discharge pipe         | Qreal (m³/h) | Hreal (m) | Power (kW) | NPSHr (m) | NPSH margin (m) | Q-deviation |
| -------------------------------- | ---------------------- | ---------------------- | ------------ | --------- | ---------- | --------- | --------------- | ----------- |
| **050-032-125.1_d104mm_3500rpm** | PEAD-PN10 DE50 (44 mm) | PEAD-PN10 DE50 (44 mm) | **14.48**    | **15.00** | **1.051**  | 1.743     | **5.98**        | **+44.8%**  |

*Why this combo wins*

* **NPSH** – margin 5.98 m comfortably exceeds the required safety margin.
* **Power** – 1.05 kW is the lowest of the passing candidates.
* **Pipe size** – using the smallest commercial diameter on both suction and discharge keeps material cost down.
* **Other candidates** – the pumps rejected for NPSH had margins below 0.5 m; the remaining passing candidates either consume more power or require larger pipe without an NPSH advantage.

**Conclusion**

With the pump located 2 m above the source level, the system still
enjoys a generous NPSH margin (≈6 m). The recommended solution is the
DE50 / DE50 pipe pair with pump `050-032-125.1_d104mm_3500rpm`.

**What this shows:** the agent explicitly accounts for suction lift
when calculating NPSHa rather than treating the NPSH condition as
unchanged from a flooded-suction case.

---

## 05 — elicitation: suction/discharge split not given

**Task**

> Two reservoirs 100m apart, static head 15m, flow 20 m3/h. Select a pump.

**Response**

To compute the real system head (static + friction) and to check NPSH
we need the pipe lengths split between the suction and the discharge
legs, and the elevation of the source liquid surface relative to the
pump (suction_static_head).

You gave:

* total pipe distance = 100 m
* total static head = 15 m
* required flow = 20 m³/h

What I still need:

1. **How long is the suction pipe and how long is the discharge pipe?**
   (e.g., 30 m suction + 70 m discharge, or any other split you prefer.)
2. **What is the suction static head** – the vertical distance between
   the liquid level in the source reservoir and the pump centreline.

   * Positive → source above the pump (flooded suction).
   * Negative → source below the pump (suction lift).

If you prefer to ignore NPSH for now, just let me know, and we can
proceed with a default split (e.g., 20 m suction, 80 m discharge) and
assume a flooded suction (suction_static_head = +2 m). Otherwise,
provide the exact numbers and I’ll run the full pipe-pump grid and give
you the optimal pump-pipe combination.

**What this shows:** the agent recognizes that total pipe length is
not enough for the intended suction/discharge analysis and asks for
the missing geometry rather than silently guessing a split.

---

## 06 — trade-off judgment at higher flow

**Task**

> Static head 12m, flow 45 m3/h, suction 8m long, discharge 120m long, pump is 4m above the source water level. Recommend pipe sizes and a pump, and tell me if I need a bigger suction pipe for NPSH.

**Response**

**Recommended pipe-size / pump combination**

| Suction pipe    | Discharge pipe  | Pipe internal dia. (mm) | Pump model (catalog)           | Q real (m³/h) | H real (m) | P real (kW) | NPSHr (m) | NPSHa (m) | NPSH margin (m) | Q deviation |
| --------------- | --------------- | ----------------------- | ------------------------------ | ------------- | ---------- | ----------- | --------- | --------- | --------------- | ----------- |
| pead_pn10_de110 | pead_pn10_de110 | 96.8 mm (PN10 110)      | **065-040-125_d104mm_3500rpm** | 42.70         | 14.89      | **2.49**    | 2.64      | 5.89      | **3.25**        | −5.1%       |

*Why this wins*

* **Flow match** – The real operating flow is only 5% below the 45 m³/h target, the smallest deviation among all viable combinations.
* **Power** – At 2.49 kW it is the lowest-power candidate among the combinations examined with comparable flow.
* **NPSH** – With the source 4 m below the pump (−4 m suction static head), NPSHa is 5.89 m. The pump's NPSHr is 2.64 m, giving a margin of 3.25 m.
* **Cost / material** – Using the same 110 mm PN10 pipe for both suction and discharge avoids adding a larger suction diameter without a meaningful NPSH benefit.

**Do I need a larger suction pipe for NPSH?**

No.

The calculated NPSH margin (3.25 m) is comfortably above the 0.5 m
selection threshold. Upsizing the suction leg to de125 raises NPSHa
only from approximately 5.89 m to 5.98 m, increasing the margin from
about 3.25 m to 3.33 m.

The agent therefore judged that the additional pipe diameter provides
little NPSH benefit relative to the additional material cost.

**Rejected alternatives (summary)**

* Other pump models either missed the system curve or produced larger flow deviations.
* For the same pipe combination, the next-best pump,
  `065-040-125_d110mm_3500rpm`, delivered about 51 m³/h (+14%) and
  required 3.08 kW.
* Larger-diameter combinations gave slightly different operating flows
  but did not provide a meaningful NPSH advantage.

**Conclusion**

Use PN10 de110 pipe (internal ≈96.8 mm) for both suction (8 m) and
discharge (120 m) and the KSB Etanorm centrifugal pump
`065-040-125_d104mm_3500rpm`.

No larger suction pipe is required for NPSH.

**What this shows:** the agent evaluates whether increasing suction
diameter actually changes the NPSH margin enough to justify the
additional pipe, rather than automatically maximizing suction-pipe
diameter.

---

## 07 — insufficient information for full pipe-pump analysis

**Task**

> Q=15 m3/h, H=25 m, but available NPSH is only 1m.

**Response**

To size a pump you’ve supplied the target flow (15 m³/h), the total
static head (25 m) and the available NPSH (1 m).

For a realistic selection we also need the **pipe layout** so the
friction losses can be added to the static head and the NPSH margin
can be computed correctly:

1. **Suction-side length** (m) – from the liquid source up to the pump
   inlet.
2. **Discharge-side length** (m) – from the pump outlet to the delivery
   point.
3. **Suction static head** (m) – vertical elevation of the source
   surface relative to the pump centreline (positive if the source is
   above the pump, negative for a suction lift).

With these three numbers I can run the full pipe + pump grid, evaluate
NPSH margin, and pick the best pump-pipe combination. Please provide
those values (or tell me if you want to assume the pipe runs are
negligible).

**What this shows:** the scenario demonstrates that the agent does not
treat an NPSHa value alone as enough information to perform the full
pipe-pump analysis. It asks for the hydraulic layout needed by the
pipeline model.

---

## What these examples demonstrate

Across the seven scenarios, the agent is expected to do more than
return a numerical answer.

It must:

* use deterministic engineering tools for hydraulic calculations;
* find the real pump operating point rather than assuming the pump
  operates exactly at the target flow;
* distinguish pump-only selection from pipe + pump analysis;
* model suction and discharge pipes independently;
* account for suction friction when evaluating NPSHa;
* treat NPSH margin as a selection constraint;
* compare feasible candidates using engineering trade-offs such as
  flow deviation, power, pipe diameter and NPSH;
* ask for missing information when the supplied data are insufficient;
* avoid inventing geometric information that materially affects the
  engineering result.

The examples also expose a known limitation of the current LLM layer:
even when the underlying deterministic calculations are correct and
the prompt instructs the model to re-verify candidate rows, the model
can still mix values or identifiers from similar-looking rows while
composing a response. Example 03 documents this explicitly rather than
hiding the failure.
