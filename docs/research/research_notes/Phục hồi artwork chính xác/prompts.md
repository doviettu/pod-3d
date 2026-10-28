# NEW user expansion: PODStudio prompt generation
As of 2026-10-27. Research/proposal only; no paid calls, installs, or application changes. Current reconstruction is an unmeasured baseline, not verified high quality. Read researcher.md successfully; lean-ctx blocked the supplementary ~/.codex/LEAN-CTX.md. No private settings/key files were read.

## 1. What do current code and official evidence establish?
### Takeaway
Keep flare and the existing request budget. Improve the specification before introducing another paid model.

### Cited Findings
- Current code sends original mockup first, aspect guide second; thumbnails reference to 1600 pixels; requests one square PNG at 1024 or 2048, medium by default; crops the guide rectangle, saves native artwork, then separately resizes/upscales into the print canvas. It omits lettering/logos and has no automatic retry. The selected-panel ceiling of three requests is supplied task context. [Local source](/Volumes/ViTu/Claude%20Codex/POD/pod-studio/openai_api.py)
- The edit reference names flare, JSON images with image_url/file_id, data URLs, mask, n, quality, size and output_format. Its input_fidelity description does **not establish flare support**: omit it. No documented seed or negative_prompt is established here; do not invent them. [Edit reference](https://developers.openai.com/api/reference/resources/images/methods/edit)
- Flare accepts custom WIDTHxHEIGHT: edges divisible by 16, maximum edge 3840, aspect between 1:3 and 3:1, total pixels 655,360–8,294,400. Above 3,686,400 pixels is experimental, including 2048-square. Low/medium/high/xhigh/max/auto are documented; retaining medium is a test control. [Size/quality guide](https://developers.openai.com/api/docs/guides/image-generation#size-and-quality-options)
- Masks guide generation rather than guarantee exact boundaries; with multiple inputs they apply to the first. [Mask guidance](https://developers.openai.com/api/docs/guides/image-generation#edit-an-image-using-a-mask)
- Flare produces images, not structured analysis JSON. Its page lists structured outputs as unsupported and documents a dated snapshot, 2026-09-08. [Flare model](https://developers.openai.com/api/docs/models/gpt-image-2.5-flare)

### Inferences
The baseline correctly distinguishes reference/guide and separates typography. However:
- “Preserve identity” cannot lock a face; three independent panel generations cannot guarantee shared motifs.
- Generic “matching motifs” can transfer front imagery to an unseen back. Missing evidence needs a gate.
- Repetition lacks measured period, phase, orientation and landmarks. “No invention” conflicts with filling unseen areas.
- The guide adds an image and asks generation to spend output area on padding. For an ideal 1:3 rectangle inside a square, only roughly one-third of pixels become artwork: a geometric calculation, not a billing estimate.
- Freeform notes appended last can contradict constraints. Compile reviewed fields instead.
- Prompt DPI does not create print detail. Native size/aspect are API arguments; final pixels, physical dimensions and DPI metadata belong to placement. Upscaling cannot establish recovered truth.

### Gaps
No output examples, measured fidelity, token counts or per-artwork costs were evaluated. No flare-specific input_fidelity guarantee was found. Guide-mask size descriptions vary across official reference surfaces; conservatively prepare a matching-dimension RGBA PNG under 4 MB before a separately approved mask test. [Python mask reference](https://developers.openai.com/api/reference/python/resources/images/methods/edit)

## 2. How should a controlled prompt builder work?
### Takeaway
First option: human/Codex-reviewed design_spec JSON → deterministic local prompt builder. A manually prepared Codex brief could be imported later; the current web app has no import feature. Do not claim its backend silently invokes internal Codex agents or consumes the ChatGPT subscription.

### Cited Findings
OpenAI recommends explicit reference roles, separating changes from invariants, and inspecting preserved details. It specifically recommends local compositing when pixels must remain identical. Structured syntax is maintainable formatting, not a special quality mechanism. [Image prompting](https://developers.openai.com/api/docs/guides/image-prompting#prompting-fundamentals)

Optional analysis requires a separately selected vision model supporting Structured Outputs; schema compliance does not make observations true. Handle refusal/incomplete output. [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) Vision can misread small/non-English text and spatial details. [Vision limitations](https://developers.openai.com/api/docs/guides/images-vision#limitations)

### Inferences
Everything below is a proposed contract, not implemented behavior or measured improvement.

#### Minimal schema and secret-free sample
Coordinates use normalized [left,top,right,bottom] in their explicitly named image/canvas. Validate ordering and bounds locally. “Confidence” is an uncalibrated assessment, never permission.

~~~json
{
  "type":"object","additionalProperties":false,
  "required":["version","panel","images","target","facts","shared_master","locks","unknowns","languages","approved"],
  "properties":{
    "version":{"const":"1"},
    "panel":{"enum":["Front","Back","Sleeve"]},
    "images":{"type":"array","items":{
      "type":"object","additionalProperties":false,
      "required":["id","role","source_id","crop"],
      "properties":{
        "id":{"type":"string"},
        "role":{"enum":["evidence","target","geometry","shared_master"]},
        "source_id":{"type":"string"},
        "crop":{"type":["array","null"],"items":{"type":"number"},"minItems":4,"maxItems":4}
      }}},
    "target":{"type":"string"},
    "facts":{"type":"array","items":{
      "type":"object","additionalProperties":false,
      "required":["field","value","provenance","confidence","approved"],
      "properties":{
        "field":{"type":"string"},"value":{"type":["string","null"]},
        "provenance":{"type":"string"},
        "confidence":{"enum":["high","medium","low","unknown"]},
        "approved":{"type":"boolean"}
      }}},
    "shared_master":{"type":["string","null"]},
    "locks":{"type":"array","items":{"type":"string"}},
    "unknowns":{"type":"array","items":{"type":"string"}},
    "languages":{"type":"object","additionalProperties":false,
      "required":["source","instructions","artwork_text"],
      "properties":{"source":{"type":"string"},"instructions":{"const":"en"},"artwork_text":{"const":"vector_only"}}},
    "approved":{"type":"boolean"}
  }
}
~~~

~~~json
{
  "version":"1","panel":"Sleeve",
  "images":[
    {"id":"I1","role":"evidence","source_id":"mockup-A","crop":null},
    {"id":"I2","role":"geometry","source_id":"guide-sleeve","crop":null}
  ],
  "target":"gray rectangle in I2; cuff downward",
  "facts":[
    {"field":"band_direction","value":"horizontal","provenance":"human:mockup-A sleeve region","confidence":"high","approved":true},
    {"field":"period_and_phase","value":null,"provenance":"occluded","confidence":"unknown","approved":false}
  ],
  "shared_master":null,"locks":["portrait assets excluded from sleeve"],
  "unknowns":["band period and phase require measurement before sleeve request"],
  "languages":{"source":"unknown","instructions":"en","artwork_text":"vector_only"},
  "approved":false
}
~~~

#### Deterministic rules and gates
1. Validate schema plus image IDs/order, crop coordinate frame, panel/side, target dimensions and approval. Unknown critical geometry, portrait source or back evidence blocks spending. Serialize approved facts in fixed field order; identical inputs produce identical prompt text, not identical images.
2. Global mode: I1 = evidence, I2 = geometry only; neither a gray swatch nor garment silhouette is content. Output target = specified rectangle. Masked mode: I1 = approved flat target; subsequent inputs = named evidence only. Mask aligns to I1. Never mask the original mockup when the intended target is a flat master.
3. Select actual front/back/sleeve evidence per panel. Specify wearer-left/right and sleeve orientation. Do not infer unseen back from front without explicit human approval. Prefer replacing irrelevant full-scene evidence with a crop from the original before downsampling; retain surrounding landmarks. Adding crop plus full image adds input work. Savings/fidelity remain unmeasured; upsampling a crop adds no evidence.
4. For verified shared front/back motifs, approve one canonical motif/portrait master and reuse it locally with explicit transforms and panel-specific layouts. Do not regenerate or automatically mirror it. Shared references can guide style but cannot enforce identity.
5. Lock portrait/text/logo assets in a local compositor. Establish an approved flat portrait first; a photographed folded portrait is not already a flat locked asset. Keep text/logo regions out of editable patches. Preserve non-letter ornaments unless explicitly classified for separate layers.
6. Measure sleeve period P and phase φ against named cuff/seam landmarks, in flat-target coordinates: matching positions satisfy (y−φ) mod P. Track band order, widths and horizontal repeat too. Prefer approved local tiles for exact periodicity; perspective distances in a photo are not flat measurements.
7. Known facts need provenance; inferred details remain proposals. Unknowns use approved background/tile fill or pause—never silently invent a portrait, word, symbol or unseen motif.
8. Keep source language, UI language and artwork language distinct. Translate descriptive instructions into controlled English with review. Unknown text stays unknown. Literal transcripts, translations and spelling decisions stay outside image-generation prompts in vector-layer records until human review; source-image lettering is evidence to omit, not permission to reproduce. No raw user notes appended.

#### Four concise English templates
Fill placeholders only from approved fields. These are future templates, not approval to submit.

**Global baseline replacement** — same two image roles as existing code:
~~~text
Reconstruct flat 2D artwork for {panel}.
I1: garment evidence only. I2: printable rectangle geometry only.
Fill {target_rect}; preserve the white exterior.
Match {approved_subject_palette_layout}. Remove garment folds, lighting and perspective.
Use only panel-visible motifs. {approved_repeat_rules}
Omit lettering, numbers and logos; continue their approved background bands.
Unknown regions: {approved_unknown_policy}. Add no unsupported content.
Return one flat artwork image, without guide, garment or labels.
~~~

**Masked restoration** — requires a separately prepared target/mask pipeline:
~~~text
I1 is the approved flat target. I2 is evidence for {motif} only.
Restore only the transparent mask region: {single_repair}.
Match {approved_colors_scale_landmarks}. Preserve surrounding layout and identity.
Do not generate or rewrite lettering/logos. Unknowns: {approved_policy}.
Return the full target canvas. Protected assets will be restored locally.
~~~

**Targeted sleeve**:
~~~text
Extract only the {wearer_side} sleeve artwork from I1 evidence.
I2 defines geometry only; fill {target_rect}, cuff downward.
Bands run horizontally in this order: {band_order}.
Use approved period {P}, phase {phi}, and landmarks {anchors}.
Preserve {motifs_palette}; exclude torso portraits unless visible here.
Omit all lettering/logos. Unknowns: {approved_policy}.
Return flat artwork with unchanged exterior padding.
~~~

**One-step corrective edit** — only after distinct human approval:
~~~text
I1 is the reviewed flat result; I2 is evidence for this correction only.
Change only {region}: {one_measurable_correction}.
Preserve {identity_palette_layout_repeat_invariants}.
Generate no lettering/logos or unsupported details.
Return the full canvas; the local compositor retains protected pixels.
~~~

### Gaps
No importer, builder, vision-analysis path, tile extractor or compositor lock was implemented. Optional paid vision means one additional analysis request per reviewed brief, separately priced and approved; it is not hidden inside the panel allowance.

## 3. What can four future calls establish?
### Takeaway
Two cases × two prompts is a feasibility check, not proof of consistent quality.

### Cited Findings
Standard flare token rates are $5/M text input, $8/M image input and $30/M image output; cached/Batch rates differ. These are not per-image quotes. [Pricing](https://developers.openai.com/api/docs/pricing#image-generation-models) The flare page warns that the GPT Image 2 calculator does not estimate 2.5 token consumption. [Model pricing](https://developers.openai.com/api/docs/models/gpt-image-2.5-flare#pricing)

### Inferences
Future only; require approval of fixtures, prompts and spending first.

| Call | Case | Prompt |
|---|---|---|
| 1 | Front with portrait | A: exact current prompt |
| 2 | Same Front | B: deterministic reviewed brief |
| 3 | Sleeve with repeats | B |
| 4 | Same Sleeve | A |

Freeze **identical image bytes/order, masks, local compositor, model, size, quality, output format and n=1 within each pair**. Use current 1024-square/medium, no mask, identical existing placement. Only prompt organization/approved specification changes. No added reference, local copy, mask, crop, upscale change, vision call or corrective reroll in either branch. Those require separate future tests. Each attempted request consumes one of four slots; failures receive no replacement. No supported seed or repeated samples means stochastic variation remains unresolved.

Manifest: case/panel; source/crop/guide/mask hashes and roles; brief/prompt versions, exact text and hashes; model alias/snapshot; parameters; compositor/placement version; approved budget; request ID; latency/status; sanitized usage details if returned; output dimensions/hashes; reviewer and timing. Never save credentials/base64 payloads. Current code retains only aggregate usage; absent breakdown means cost attribution remains unknown.

Record observations, initially **not measured**:
- Human portrait identity pass/fail with source comparison.
- Exact text: N/A for generated art; later vector transcript match and visual glyph review, including diacritics.
- Sleeve band order, period error and phase displacement at approved landmarks.
- Protected-pixel changed count and maximum channel difference against the approved target before resizing; require zero for locked regions. Baseline has no lock, so mark unavailable rather than invent a pass.
- Manual correction minutes; latency; billed cost when available; accepted/rejected and reason.

Report both pairs, including failures. Cost per accepted panel includes all attempted calls and any separately approved analysis; zero acceptances gives no finite successful-unit estimate. Do not price aggregate input tokens at one modality rate or assume cache discounts. On the 16GB Mac, favor sequential local cropping/compositing with existing image tools; no local vision-model installation is needed for this proposal.

### Gaps
Back-panel fidelity, shared-master consistency, masked restoration, crop savings and reliability across garments remain untested. Four calls cannot establish a quality distribution or a dollar guarantee.

