# OMNI Live: Quest spatial assistant research

Research snapshot: September 19, 2026 (UTC).

Status: Research brief for team discussion, not an approved design or implementation plan. The team has not selected a flagship demo or model stack.

## Summary

The idea is a general-purpose assistant that shares the wearer's view through a Meta Quest, remembers useful observations and conversations, answers questions, and explains things through spatial overlays.

The confirmed hardware is a **Meta Quest 3S** and an **RTX 4060 with 8GB VRAM**. There are four team members with Unity experience and access to AI coding assistants.

The research supports pursuing this direction:

- Quest 3S officially supports passthrough camera access and environment depth. Unity can obtain camera metadata and place content in the physical environment.
- The prize track explicitly allows cloud model inference. The 4060 can handle selected perception workloads rather than the main omni model.
- `qwen3.8-omni-flash` accepts audio, video, images, and text, but produces text only. It would need a separate speech-output stage.
- `qwen3.5-omni-flash-realtime` is a documented alternative for realtime audio/video conversation and tool calling. Availability through the sponsor's gateway is unverified.
- A deterministic Unity drawing system can support much more than boxes and arrows. Diagrams, curves, ghost placements, animation, and known 3D assets do not require generative 3D inference.
- The main uncertainties are API access, grounding accuracy, small-object visibility, and latency on the actual setup. More engineering effort does not recover detail the camera never captured.

The preliminary recommendation is to keep the assistant general-purpose while demonstrating it through a specific physical task. A workspace or assembly copilot is one promising direction, but the team should choose the scenario.

## How to read this brief

Three Muse Spark 1.3 research agents investigated the model/API, Quest platform, and local perception/3D options. The coordinating assistant checked the main conclusions against primary documentation.

- **Documented:** A capability or requirement stated in a linked primary source.
- **Proposed:** An architecture or product recommendation from the research.
- **Unverified:** Something that needs a hardware experiment, authenticated API test, license check, or organizer confirmation.

No model was installed or benchmarked on the 4060. No Quest build was tested, sponsored key used, or paid API request made. Published results on A100/H100 GPUs are not performance measurements for this setup. Model IDs, gateway support, and SDK requirements should be checked again when implementation begins.

## 1. Prize track requirements

The [Huawei OMNI Live challenge repository][track] is the primary source for eligibility and judging.

### Eligibility

The submission must:

- Be a functional, demo-ready prototype built during Hack the North.
- Use an OMNI multimodal model, with Qwen3.5-Omni given as an example.
- Address a clear real-world or edge-device scenario rather than a generic chatbot.
- Meaningfully combine vision/video, speech/audio, and language.
- Demonstrate at least one complete user scenario and meaningful interaction with the model.
- Include a project repository with basic setup instructions and dependencies.

Cloud APIs are explicitly allowed. Local model deployment, training, and fine-tuning are not required. Static mockups and concept-only presentations are not eligible.

### Judging

| Criterion | Weight |
| --- | ---: |
| Scenario value and creativity | 30% |
| Use of OMNI capabilities | 25% |
| Demo completeness | 20% |
| Interaction experience | 15% |
| Technical implementation | 10% |

The track also mentions thoughtful multi-device collaboration, local optimization, latency reduction, privacy, and safety-aware design.

Implication: demonstrate why the modalities need each other. A user handling a physical task while speaking, referring to objects, and receiving spatial guidance tells a clearer story than several unrelated feature demos.

### Credits and prizes

The repository advertises CAD $40 in sponsored API credit per approved team through yibuapi. It lists 200 keys, distributed first-come, first-served, and says each team may apply only once. Recipients are expected to submit to the Huawei OMNI Live track.

- Application: <https://luma.com/0fhypcu0>
- Sponsor gateway pricing: <https://yibuapi.com/pricing>
- First place: Huawei Watch GT 6 for each team member, plus a team office tour.
- Second place: Huawei FreeClip 2 Earbuds for each team member, plus a team office tour.

This research did not apply for credits or check remaining availability. Do not assume the credit will cover continuous streaming without measuring actual gateway billing and context growth.

## 2. Product possibilities

The central interaction could be:

> Observe what is happening, remember useful evidence, understand the user's request, show an explanation in AR, and check what happens next.

The system should be able to use a previous observation and a fresh camera view in the same interaction.

### Spatial questions

Examples:

- "What am I looking at?"
- "Where is the screwdriver?"
- "Which connector does this cable fit?"
- "What changed since the last step?"

Use hand/controller pointing or a head-directed reticle to resolve "this" and "that one." Quest 3S does not provide eye tracking; head direction is not eye gaze.

### Memory grounded in observations

Examples:

- "Where did I leave the part I was using?"
- "Which of these did I say was broken?"
- "What was I doing before I got interrupted?"
- "Remember that this drawer contains M3 screws."

The distinction between "I can see it there now" and "I last saw it there two minutes ago" should appear in both speech and visual feedback. The assistant cannot know what happened outside its view.

### Spatial explanations

Examples:

- Draw an arrow from a cable to its connector.
- Animate how a bracket rotates into place.
- Show a ghosted component at an intended placement.
- Put a circuit diagram or graph beside a real object.
- Explain a mechanism using simple animated geometry.
- Show an exploded view when a suitable model and part hierarchy are available.

An illustrative explanation should not look like a verified measurement or exact reconstruction. Precise fit guidance needs known dimensions, reliable calibration, or an appropriate CAD model.

**Amended 2026-09-20.** Layout mode (`docs/omni-layout-mode.md`) gives fit guidance without any of those three, and the warning above is why it is built the way it is. It has approximate dimensions on a depth-sensed floor (calibrated only as well as the headset's depth is) and no CAD model at all. It therefore never claims precision: every clearance line is hedged, overlap is reported as overlap, the arithmetic errs towards "tight", and a caption reading "Approximate sizes, not measured." is on screen whenever furniture is. Read this paragraph as the constraint the feature is designed against, not as a warning it ignored.

### Closed-loop task assistance

A task-aware assistant could explain a step, indicate the relevant part, observe the result, and confirm progress. If it cannot see the connection or distinguish alternatives, it should ask for another view instead of inventing a confirmation.

Possible extensions include spatial bookmarks, task checkpoints, reminders tied to an object or location, and a timeline of observed changes. A historical thumbnail is a much easier and more honest first memory replay than claiming to reconstruct a complete past 3D scene.

## 3. Quest 3S and Unity feasibility

### Documented capabilities

Meta's current [Passthrough Camera API sample][camera-samples] supports Quest 3 and Quest 3S. It uses the MRUK `PassthroughCameraAccess` component and exposes:

- Camera textures.
- Camera intrinsics and pose/extrinsic information.
- Precise timestamps for camera/world alignment.
- Simultaneous left and right camera access.
- Examples for camera-to-world mapping and object detection.

The sample repository lists Unity 6000.0.38f1 or newer, MRUK v81 or newer, Horizon OS v74 or newer, and the `horizonos.permission.HEADSET_CAMERA` permission. These are the requirements of the inspected sample, not a promise that every newer package combination works without testing.

The [camera overview][camera-overview] documents 1280x960 and an additional 1280x1280 option introduced in Horizon OS v83. The accessible feed covers less than the wearer's full passthrough view. Select and test a supported resolution explicitly; do not assume a fixed aspect ratio across OS versions.

The [Depth API documentation][depth-overview] explicitly supports Quest 3S. Its lack of the Quest 3's dedicated depth sensor does not mean the environment-depth API is unavailable. Depth supports environmental occlusion and surface raycasting. Its minimum effective range is approximately 0.2 metres; closer estimates are unreliable.

The [MRUK environment raycast API][environment-raycast] provides a supported route for placing content on real surfaces. Scene geometry and spatial anchors have different jobs: room geometry describes surfaces, while anchors maintain spatial reference. Neither automatically identifies or tracks loose objects.

### Recommended development arrangement

Run the Unity application natively on the Quest. Keep rendering, head tracking, spatial placement, and immediate interaction feedback on-device. Connect to a laptop backend over LAN for model orchestration and optional local GPU inference.

The current sample supports passthrough camera preview with a physical headset or Meta Horizon Link v2.1 or later; XR Simulator does not support this camera API. Use Link for iteration where available, but validate the demo and latency on the native build.

### Grounding a camera detection in the world

A proposed pipeline:

1. Capture a frame and record its frame ID, timestamp, resolution, intrinsics, and capture-time pose.
2. Keep the crop/resize transform if preprocessing changes the image.
3. Detect an object or resolve a phrase to an image region.
4. Map the result back into the original camera image.
5. Convert the selected image point into a world-space ray using that frame's camera geometry.
6. Resolve a surface or object location using compatible spatial data, or re-observe when the available depth/scene information is too stale.
7. Render a marker and update, revalidate, or expire it as evidence changes.

Do not convert a delayed detection using the current head pose. Also, capture-time pose alone does not solve temporal mismatch: the object or surface may have moved while inference was running. A current depth raycast cannot prove where a moving object was in an old frame.

A 2D bounding box does not establish a full 3D object box. Table-level pins, rings, and labels are reasonable first outputs; precise volume and orientation estimates need additional evidence.

### Hardware limits to respect

- Tiny objects may occupy only a few useful pixels. Cropping enlarges existing detail; it does not create missing detail.
- Depth near small edges, reflective surfaces, or very close objects needs empirical testing.
- An anchor marks a place. Object tracking must separately determine whether the object remains there.
- Occlusion can hide a virtual marker behind real geometry, but does not verify object identity.
- Persistent memory across application restarts requires a way to relocalize saved positions. Old session coordinates alone are insufficient.

## 4. Omni model and sponsor API options

The [Alibaba omni model overview][omni-overview] documents these options:

| Candidate | Documented capabilities | Consequence for this project |
| --- | --- | --- |
| `qwen3.8-omni-flash` | Text, image, audio, and video input; text output; function calling | Candidate for the main reasoning/tool loop, with a separate TTS stage |
| `qwen3.5-omni-flash-realtime` | Realtime audio/video interaction over WebSocket; function calling | Candidate for conversational speech and AR tool use |
| `qwen3.5-omni-plus-realtime` | Realtime audio/video interaction over WebSocket; function calling | Alternative to compare if access, latency, and cost permit |
| `qwen3-omni-flash-realtime` | Realtime multimodal interaction; function calling listed as unsupported | Would need a separate tool-driving path |

The dedicated [Qwen3.8 model page][qwen38] confirms that its output modality is text. Its name should not be taken to imply native speech generation.

### Two reasonable application architectures

**Realtime omni model:** Send live audio and selected camera frames to the realtime endpoint. Receive conversational output and function calls. This is attractive for interruption and natural turn-taking if the gateway supports the required interface.

**Omni understanding plus TTS:** Use Qwen3.8 for multimodal understanding and structured tools, then synthesize its response with a streaming TTS service. This adds another component but gives explicit control over speech scheduling and cancellation. HTTP transport does not prevent the application from feeling responsive, although it is not the same interface as a native realtime model.

Neither option is selected. A second omni model for heavier analysis is optional; it should solve a measured limitation rather than be assumed necessary.

The [realtime SDK documentation][realtime-sdk] states that a tool-calling response returns tool arguments without generated audio. Execute the tool, return its result, and request the follow-up response. Speech and tool actions need coordination rather than an assumption that both arrive together.

### The unresolved sponsor gateway question

The challenge supplies credit through yibuapi. Public material describes an OpenAI-compatible HTTP gateway, but the research did not establish whether the sponsored key supports:

- The exact Qwen model IDs above.
- A realtime WebSocket endpoint.
- All required image/audio input fields and streamed outputs.
- Function calling and tool-result messages for the assigned model/group.
- The same limits and pricing as upstream Alibaba APIs.

Ask the organizers for the supported endpoint, model ID, and a working multimodal example. Do not assume a sponsor key works against a direct Alibaba endpoint, and do not send it to an unrelated service to test that assumption.

Qwen3.8 plus TTS is not automatically ineligible. The track requires meaningful vision, audio, and language integration; it does not require one specific speech-output architecture. Ask organizers about any ambiguous eligibility detail rather than treating this brief as an eligibility ruling.

## 5. Perception on an 8GB RTX 4060

Use the GPU for specific jobs and measure peak VRAM as well as latency. Do not plan to keep every candidate resident at once.

| Need | Candidate | Evidence and caveat |
| --- | --- | --- |
| Language-conditioned boxes and points | NVIDIA LocateAnything-3B | Documented phrase grounding and localization; 8GB fit and 4060 latency are unverified |
| Open-vocabulary detection | Grounding DINO | Established text-conditioned detection option; choose a checkpoint and benchmark it |
| Fast detection of known categories | Small YOLO-World variant | Useful candidate for a fixed task vocabulary; published GPU results do not establish 4060 FPS |
| Masks and video tracking | Small SAM 2 variant; EfficientTAM as an alternative | Candidate for tracking after a target is selected; segmentation does not itself establish semantic identity |
| Text on labels and manuals | Florence-2-base or a dedicated OCR pipeline | Can help identify bins and component markings; test on headset imagery |

Primary references: [LocateAnything model card][locate-anything], [Grounding DINO][grounding-dino], [YOLO-World documentation][yolo-world], [SAM 2][sam2], and [Florence-2-base][florence].

### LocateAnything is the NVIDIA model mentioned in the idea

`nvidia/LocateAnything-3B` supports object detection, referring-expression grounding, text localization, and point outputs. It is a plausible query-time grounder for requests such as "the part beside the blue box."

Two constraints need attention:

1. The model card's published inference tests use larger GPUs. Its name is not a VRAM guarantee. Quantization, lower input resolution, crop-based queries, or offload may be necessary, and their accuracy/runtime must be tested.
2. Its model card specifies academic/non-profit research use and prohibits commercial use except by NVIDIA and affiliates. Check the full license and whether the planned use qualifies before adopting it. Do not assume a prize submission or later startup use is covered.

### A restrained starting stack

Start with one grounder, cloud omni reasoning, and Unity markers. Add a lightweight tracker if repeated localization is too slow or unstable. Add dedicated OCR only if labels are important and the existing model does not handle them well enough.

The principle is to keep local work tied to demonstrated needs, not to reduce the ambition of the product. A model scheduler can serialize heavy jobs, but model loading and offload have latency costs too.

### The M3 screw example

Credible approaches include identifying a labeled M3 bin, remembering the user's label for a part, matching an enrolled object, or using a known size reference and sufficient image detail.

Do not claim that the system can reliably distinguish M3 from M4 or similar fasteners using an arbitrary distant monocular image. Metric size and thread details require evidence. Even stereo or environment depth does not guarantee the precision needed for tiny parts.

A useful response can be: "The M3 screws were in the left tray. I can see that tray now; move closer if you want me to distinguish individual parts."

## 6. AR visualization and generative 3D

### Proposed drawing harness

Use a constrained scene-description interface that Unity renders deterministically. The model chooses what to show; Unity maintains the geometry and placement between model responses.

| Tool family | Examples |
| --- | --- |
| Attention | Ring, outline, label, spotlight, off-screen direction indicator |
| Relationships | Connect objects, group parts, show a path or movement direction |
| Geometry | Lines, curves, planes, volumes, axes |
| Diagrams | Graphs, flowcharts, circuit explanations, annotated panels |
| Demonstration | Ghost placements, rotation animations, step sequences |
| Assets | Load a known GLB/CAD asset, position it, animate supported parts |

Commands should reference object IDs, observations, or resolved spatial targets. The harness should validate parameters, enforce size and placement limits, cap visual clutter, support undo/clear, and return execution results to the model. Do not execute arbitrary model-generated C#.

Treat text found in the environment as scene data, not instructions that can override the assistant or authorize tools.

### Where generated meshes help

Generated assets can be useful for illustrative objects that the primitive library cannot express. They should load asynchronously while the conversation and existing overlays remain usable.

[TripoSR's official repository][triposr] states that default single-image inference uses about 6GB VRAM. That makes it a reasonable local experiment, not a measured claim about speed or usable output quality on the 4060. It reconstructs from an image; it is not a complete text-to-3D pipeline by itself.

The original [TRELLIS repository][trellis] lists a minimum of 16GB VRAM. Low-memory forks and optimized Hunyuan3D variants may warrant later experiments, but this research did not establish a dependable 8GB configuration for them.

For the first version, prefer:

1. Procedural Unity geometry for explanations.
2. Known assets with appropriate dimensions and pivots.
3. Cached or asynchronous generated assets for illustrative detail.

Generated meshes can invent unseen surfaces and have arbitrary scale. They should not determine whether a real component fits or provide unverified safety-critical instructions.

## 7. Proposed memory and runtime architecture

This is a discussion sketch, not a final design.

### Memory structure

Keep a short rolling buffer for recent context, an event history for meaningful observations, and a store of persistent user-taught facts. These serve different retention needs.

A useful observation record could include:

- Observation ID, frame ID, and timestamp.
- Object ID or candidate identity, with confidence.
- Image crop and relevant audio/transcript evidence.
- Camera pose and any resolved location, including its coordinate frame.
- Whether the object is currently observed or only remembered.
- Links to a task step, named location, or user correction.

Extract events from scene changes, interactions, task transitions, and explicit "remember this" instructions. Store enough original evidence to inspect an answer. Avoid repeatedly compressing model-generated summaries until an inference becomes indistinguishable from an observation.

A small event database and evidence files are sufficient to begin. Embeddings can help retrieval; they do not replace identity tracking or timestamped records.

Provide visible capture state, pause, delete/forget, and session-clear controls. Define what leaves the headset and what remains local. Bystanders may be present in the camera/audio stream, so retention should be deliberate.

### Separate loops

| Loop | Responsibility |
| --- | --- |
| On-device rendering and interaction | Maintain stable visuals, track the headset, handle pointing, and show immediate feedback |
| Conversation and reasoning | Interpret speech and selected imagery, retrieve memory, choose tools, and speak |
| Perception and memory updates | Ground targets, update tracks, detect meaningful changes, and record evidence |

The rendering loop should never wait for a cloud response to keep a marker stable. The perception loop should not send every camera frame to the main model by default. Use selected frames and task-driven close-ups; determine actual sampling rates from latency, quality, bandwidth, and cost tests.

When data is stale, the application should fade or remove uncertain overlays, ask for another look, and avoid claiming that an old location is current. When the network fails, keep the app responsive and make the unavailable functions clear.

## 8. Possible demo directions

| Direction | Strength | Main risk |
| --- | --- | --- |
| Workspace/assembly copilot | Naturally combines object finding, memory, speech, spatial guidance, and progress checks | Requires a physical task whose relevant details are visible and groundable |
| Spatial tutor | Strong visual explanations, diagrams, and interactive models | Could become a graphics demo with weak use of real-world context |
| Everyday companion | Closest to the unrestricted assistant vision | Harder to show a distinctive complete workflow in a short demo |

The research recommendation is a workspace copilot as the first demonstration, with spatial teaching as one capability. This does not commit the team or limit the underlying architecture to that scenario.

An example sequence, if that direction is chosen:

1. The user identifies a parts tray and puts down a tool while speaking.
2. The assistant helps with an assembly step.
3. The user interrupts to ask where the tool went.
4. The assistant retrieves the earlier observation and guides attention to the last-seen location.
5. A fresh view confirms whether the tool is still there.
6. The assistant shows how a bracket fits using a known model or clearly illustrative geometry.
7. The user makes a visible error; the assistant notices a discrepancy or requests a better view.
8. The user asks what remains, and the assistant answers from task history.

This sequence is a candidate to test, not a promise that arbitrary assembly mistakes can be detected.

## 9. Validation before committing to a stack

These experiments have not been run.

| Experiment | What it should establish |
| --- | --- |
| Build Meta's camera sample on the actual Quest 3S | Permissions, OS compatibility, usable resolution, frame access, and camera metadata |
| Place a marker from a captured image point | Coordinate correctness and stability while the wearer moves |
| Test delayed detections and moved objects | Whether stale results are rejected or revalidated instead of producing misleading markers |
| Inspect screws, labels, and connectors through the camera feed | Whether the proposed demo contains enough visible detail |
| Use the issued sponsor endpoint with an organizer-provided example | Exact model availability, multimodal fields, streaming, tools, and realtime transport |
| Exercise a complete tool-call round trip | Model request, validated Unity action, result acknowledgement, and spoken follow-up |
| Test speech interruption with headset playback | Cancellation, echo behavior, and turn-taking in the intended environment |
| Benchmark one local grounder on recorded Quest frames | Peak VRAM, latency, successful localization rate, and failure cases |
| Retrieve a deliberately moved or occluded object from memory | Honest separation of current visibility, last-seen evidence, and uncertainty |
| Run the full candidate scenario repeatedly | Completion rate, first-audio latency, time to useful overlay, drift, and recovery behavior |

Record both typical and slow responses. Avoid relying on a single successful run or transferring published server-GPU numbers to the 4060. Test on the network intended for the demo.

Any setup, installs, credentials, or paid calls need a separate implementation decision; this document authorizes none of them.

## 10. Team decisions still open

- Which real-world scenario should anchor the demo?
- Which capabilities are essential to that scenario, and which are optional demonstrations of the general system?
- Does the team already have a sponsored key, and what interfaces does it expose?
- If needed, what direct API access and budget are available outside the sponsor gateway?
- Which Horizon OS version is on the Quest 3S, and can the team deploy native builds?
- What tabletop, parts, lighting, and network will be available?
- Should memory persist only within a demo session or across application restarts?
- What audio/image retention and cloud-upload policy should the team adopt?
- Are known labels, enrolled parts, and prepared CAD assets acceptable for the chosen scenario?
- What legal/license constraints apply to the intended submission and any later commercial use?

If implementation follows, a reasonable four-person split is Quest sensing/rendering, conversation/API integration, perception/tracking, and memory/task state plus demo integration. Agree on frame IDs, object IDs, coordinate conventions, timestamps, cancellation, and tool-result formats before separate workstreams depend on them.

## Sources

Primary sources carry the documented claims above. Architectural suggestions and proposed experiments are research recommendations.

1. [OMNI Live challenge repository][track]: eligibility, judging, credits, and prizes.
2. [Meta Unity passthrough camera samples][camera-samples]: supported hardware, sample requirements, camera metadata, and preview support.
3. [Meta Passthrough Camera API overview][camera-overview]: supported cameras, resolution, and field-of-view limitations.
4. [Meta Depth API overview][depth-overview]: Quest 3S support, occlusion, raycasting, and minimum effective range.
5. [Meta MRUK environment raycast][environment-raycast]: supported surface-placement interface.
6. [Alibaba omni model overview][omni-overview]: model/API distinctions and function-calling support.
7. [Qwen3.8-Omni-Flash model page][qwen38]: input and output modalities.
8. [Alibaba realtime Python SDK][realtime-sdk]: tool calling, audio flow, and interaction examples.
9. [NVIDIA LocateAnything-3B model card][locate-anything]: grounding capabilities, deployment information, and license restrictions.
10. [Grounding DINO repository][grounding-dino]: open-vocabulary detection candidate.
11. [Ultralytics YOLO-World documentation][yolo-world]: detection candidate and vocabulary setup.
12. [Meta SAM 2 repository][sam2]: promptable segmentation and video tracking.
13. [Microsoft Florence-2-base model card][florence]: OCR and other vision tasks.
14. [TripoSR repository][triposr]: single-image reconstruction and documented default VRAM use.
15. [Microsoft TRELLIS repository][trellis]: original model requirements.

[track]: https://github.com/cari-waterloo-rc/OMNI-Live-Build-the-Next-Generation-of-Real-Time-Multimodal-AI
[camera-samples]: https://github.com/oculus-samples/Unity-PassthroughCameraApiSamples
[camera-overview]: https://developers.meta.com/horizon/documentation/unity/unity-pca-overview/
[depth-overview]: https://developers.meta.com/horizon/documentation/unity/unity-depthapi-overview/
[environment-raycast]: https://developers.meta.com/horizon/documentation/unity/unity-mr-utility-kit-environment-raycast
[omni-overview]: https://docs.modelstudio.console.alibabacloud.com/en/model-studio/omni
[qwen38]: https://docs.modelstudio.console.alibabacloud.com/en/model-studio/qwen3-8-omni-flash
[realtime-sdk]: https://help.aliyun.com/en/model-studio/omni-realtime-python-sdk
[locate-anything]: https://huggingface.co/nvidia/LocateAnything-3B
[grounding-dino]: https://github.com/IDEA-Research/GroundingDINO
[yolo-world]: https://docs.ultralytics.com/models/yolo-world/
[sam2]: https://github.com/facebookresearch/sam2
[florence]: https://huggingface.co/microsoft/Florence-2-base
[triposr]: https://github.com/VAST-AI-Research/TripoSR
[trellis]: https://github.com/microsoft/TRELLIS
