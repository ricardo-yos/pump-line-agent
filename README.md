# pump-line-agent

An engineering agent that selects a centrifugal pump — and, in its
second stage, sizes the suction/discharge piping around it — for a
water-transfer duty between two reservoirs.

The project combines an LLM agent with deterministic hydraulic
calculations, commercial pump curves, and a PEAD (HDPE) pipe catalog.
The agent interprets a free-text engineering task, decides which
calculations are needed, calls the appropriate tools, evaluates the
results, and makes the final engineering recommendation.

## Architecture

The agent follows the same incremental pattern as
[readytensor/building-agents](https://github.com/readytensor/building-agents):

```text
User task
    ↓
LLM agent
    ↓
Tool selection / orchestration
    ↓
Deterministic engineering calculations
    ↓
Results
    ↓
LLM interpretation and decision
```

The implementation uses a plain Python `while` loop and tools registered
via a `@tool` decorator that builds each tool's JSON schema from its
signature. No agent framework is used.

Two pieces from the reference project were deliberately not adopted:
context compaction (Episode 3) and the sandbox-reset scaffolding
(Episode 2's `main()`).

See **Known limitations** for the practical consequence of not having
context compaction.

## Two modes

The agent takes a single free-text engineering task rather than
structured fields. It determines from the description which mode
applies.

### Pump only

You already know the required flow (Q) and head (H).

The agent evaluates the available 3500 rpm pump/impeller
configurations and returns the candidates that satisfy the deterministic
selection gates. It then chooses among the passing candidates based on
the engineering trade-offs visible in the results.

System piping is not modeled in this mode, so the required head is
treated as fixed.

### Pipe + pump

You provide a target flow, the static elevation between two reservoirs,
and the suction/discharge pipe lengths.

The agent evaluates commercial pipe diameter combinations, builds the
corresponding system curves from static head plus Darcy-Weisbach
friction loss, and finds the actual operating point where each pump
curve intersects the system curve.

This is the physically richer version of pump-only selection because
the required pump head depends on the piping configuration.

If NPSH information is available, the agent also evaluates NPSH margin,
including the effect of suction-side friction.

If essential information is missing, such as flow, head, pipe lengths,
or the source reservoir elevation relative to the pump, the agent asks
for it instead of guessing.

## Key engineering decisions

The project deliberately keeps its engineering scope constrained.
These are design decisions rather than accidental omissions.

* **3500 rpm is fixed in the benchmark catalog.** The benchmark catalog
  contains pump curves at 3500 rpm, and rotational speed is not treated
  as an optimization variable.

* **No trim, no VFD.** The agent selects among published commercial
  pump curves (fixed impeller diameter, fixed speed). It does not assume
  arbitrary impeller trimming or variable-frequency operation. Commercial
  selection tools optimize over these additional degrees of freedom; this
  project intentionally keeps the search space limited to published
  catalog points.

* **Polynomial degree is per-curve, not fixed.** Real pump curves deviate
  from the idealized quadratic behavior predicted by turbomachinery theory.
  `fit_curve.py` and `batch_fit.py` test multiple polynomial degrees and
  select the lowest degree that achieves an adequate fit, avoiding
  unnecessary overfitting of digitization noise.

* **NPSHr interpolation is used when appropriate.** Pump catalogs often
  publish NPSHr curves for only selected impeller diameters.
  `pump_sizing.py` linearly interpolates between the available curves for
  intermediate diameters represented by the catalog.

* **The pump is never assumed to hit the target flow exactly.** The real
  operating point is found by root-finding where the pump curve intersects
  the system curve (`find_operating_point`). The reported flow is therefore
  the physically predicted operating point, not simply the requested duty.

* **No automatic "best" pick.** Tools such as `select_pump` and
  `compute_pipe_pump_grid` return candidates that pass deterministic
  engineering constraints (head, operating range, NPSH margin). The final
  selection is left to the LLM because it represents a real engineering
  trade-off: energy consumption, flow matching, pipe sizing, and NPSH
  margin are not always optimized by the same choice.

* **Rejected candidates are summarized by category.** The tools return
  rejection counts rather than every rejected candidate, keeping tool
  payloads manageable as the catalog grows.

* **Suction and discharge are independent legs.** Each side has its own
  diameter and length. Suction friction affects NPSHa, while discharge
  friction affects the system curve. The grid only evaluates cases where
  suction diameter is equal to or larger than discharge diameter, reflecting
  common engineering practice for protecting suction conditions.

* **Tool parameters include units explicitly.** Parameters use names such
  as `Q_target_m3h` and `H_required_m` instead of ambiguous names like
  `Q_target`. This came from an observed failure mode where the LLM
  converted units incorrectly before calling a tool. Making units visible
  in the schema reduces this ambiguity.

## Engineering model

The hydraulic model currently uses:

* Darcy-Weisbach friction loss;
* Colebrook-White friction factor for turbulent flow;
* laminar friction factor for `Re < 2300`;
* root-finding to determine pump/system operating points;
* NPSHa and NPSHr comparison;
* commercial PEAD PN10/SDR17 pipe dimensions;
* room-temperature water properties;
* straight-run pipe friction only.

The pump selection logic applies deterministic feasibility gates before
the LLM evaluates the passing candidates.

This separation is intentional:

```text
LLM
  → interprets the engineering task
  → selects and chains tools
  → evaluates alternatives
  → explains the recommendation

Deterministic layer
  → hydraulic equations
  → pump curves
  → pipe curves
  → operating points
  → NPSH calculations
  → feasibility gates
```

The LLM therefore does not perform the hydraulic calculations itself.

## Building the pump catalog

`pump_catalog.py` contains the processed pump catalog used by the
agent. The catalog entries are derived from digitized manufacturer
performance curves and include source metadata for traceability.

The benchmark catalog was defined using the official KSB Etanorm
selection chart. Pump models were selected to cover the target operating
range of this project, approximately Q = 10–50 m³/h and H = 15–40 m.

The objective was not to reproduce the complete manufacturer portfolio,
but to create a representative benchmark catalog covering the intended
design space.

The repository does not include the original manufacturer catalog PDFs
or raw source documents. The digitized curves and processed coefficients
should be treated according to the original source terms.

To build or extend the catalog from a manufacturer's catalog PDF:

1. Digitize each curve (H, P, NPSH per model/diameter) using a tool such
   as WebPlotDigitizer, exporting points to CSV under
   `data/raw_curves/<model>/d<diameter>mm_<H|P|NPSH>.csv`.
   A bare `NPSH.csv` with no diameter prefix represents one shared NPSH
   curve for every diameter of that model.

2. Run:

```bash
python batch_fit.py --root data/raw_curves --out fit_results.json
```

This tests polynomial degrees 2–4 per curve and writes
`fit_results.json`.

3. Generate catalog entries:

```bash
python build_catalog_entries.py \
    --input fit_results.json \
    --document "1311.46/12-EN-US"
```

This creates `catalog_entries_draft.py` containing
`GENERATED_PUMP_CATALOG` and `GENERATED_NPSH_CURVES`.

4. Review the generated draft, especially the source `page` fields,
   which the script cannot infer, and merge the reviewed entries into
   `pump_catalog.py`.

## Setup

Create a virtual environment and install the dependencies:

```bash
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Create the environment configuration file:

```bash
cp .env.example .env
```

Fill in the required API key and model settings in `.env`, then run:

```bash
python agent.py
```

The project uses an OpenAI-compatible client interface and can switch
providers by changing `LLM_BASE_URL` and `LLM_AGENT_MODEL`. The matching
API key is selected automatically by `make_client()`.

`LLM_MAX_TOKENS` and `LLM_REASONING_EFFORT` are optional configuration
parameters used when working with providers that impose tight
per-minute token limits.

## Examples and benchmark scenarios

The repository includes manual engineering scenarios under `scenarios/`
covering:

* simple pump selection;
* pipe + pump sizing without NPSH data;
* flooded suction with NPSH evaluation;
* suction lift with NPSH evaluation;
* missing-information elicitation;
* pump/pipe trade-offs at higher flow;
* an infeasible pump-only request.

The corresponding execution examples are documented in [`EXAMPLES.md`](EXAMPLES.md).

These scenarios are manual regression experiments rather than an
automated test suite. Each scenario is run independently from a fresh
agent context, and the resulting tool calls and final response can be
inspected to evaluate whether the agent used the engineering tools
correctly.

Run:

```bash
python run_scenarios.py
```

Generated transcripts are saved under `runs/` and are intentionally not
versioned.

## Known limitations

* **No context compaction by design.** The reference architecture
  includes context compaction for long tool-calling conversations, but it
  was deliberately not adopted in this project.

  In this engineering workflow, tool results contain numerical evidence
  required for later decisions. Values such as operating points, NPSHa,
  power, and candidate comparisons cannot always be safely reduced to a
  generic summary without losing information relevant to engineering
  judgment.

  The trade-off is increased context usage and possible provider token
  limits during long analyses. During development, this occurred with
  Groq's free tier. The project mitigates this by reducing unnecessary
  tool payloads and improving candidate browsing.

  For larger catalogs or longer workflows, a domain-aware compaction
  strategy would be needed to preserve the numerical relationships
  required for engineering comparison.

* **Only straight-run friction loss is modeled** (Darcy-Weisbach).
  Local losses from fittings, valves, entrances, exits, and other components
  are not included.

* **PEAD PN10/SDR17 only.** The current pipe catalog assumes one pressure
  class suitable for the project's intended head range. Pressure class is
  not dynamically selected per scenario.

* **Room-temperature water only.** Vapor pressure and kinematic
  viscosity are fixed in `pipe_sizing.py` rather than being parameterized
  by fluid and temperature.

* **Catalog coverage is limited.** The agent can only select among the
  pump models, impeller diameters, and performance curves that have
  actually been digitized.

* **3500 rpm only.** The benchmark does not compare different rotational
  speeds, VFD operation, or pump families outside the digitized catalog.
  Results represent the best recommendation within the modeled catalog
  scope, not a universal pump-selection optimization.

## Data sources and attribution

This project uses publicly published manufacturer documentation as the
source for its engineering data.

### Pump data

The pump performance curves used to build the benchmark catalog were
digitized from published **KSB Etanorm** technical documentation.

The project does not redistribute the original KSB catalogs, scanned
pages, or raw digitized curve data. The repository contains only the
derived curve coefficients required by the software, together with
source metadata identifying the manufacturer, document, and page.

KSB and Etanorm are trademarks and/or product names of their respective
owners. This project is independent of and not affiliated with KSB.

### Pipe data

The PEAD (HDPE) dimensional data used by the pipe catalog are based on
**Fersil PE100 PN10/SDR17** technical documentation, with the applicable
dimensions referenced in `pipe_catalog.py`.

Fersil is used as a manufacturer reference for commercial pipe
dimensions. The project does not represent Fersil as the normative
authority for the underlying EN 12201 standard.

The project does not redistribute Fersil technical datasheets or other
copyrighted manufacturer documents.

### Code and third-party data

The project's source code is licensed under the license specified in
[`LICENSE`](LICENSE).

Manufacturer names, trademarks, technical documents, and source data
remain the property of their respective owners. Their identification in
this project is solely for source attribution and reproducibility.
