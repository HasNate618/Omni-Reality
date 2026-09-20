# Viewer verification notes (2026-09-19, headless bench box)

`viewer_client.py` is eyeball tooling; the automated coverage for the
server path is the Task 5 GPU round-trip (real tiny model, non-empty mask).

Verified headlessly on the bench box:
- `import sam2ws.viewer_client` clean; `decode_mask` round-trips the
  `mask_png_b64` codec exactly (12/12 px on a synthetic mask).
- `cv2.VideoCapture(0)` opens and yields 640x480 frames (camera present).
- Threading/queue structure reviewed; send cap (10 FPS), size-1 capture
  slot, `evicted` freeze behavior implemented per plan.
- `--click x,y` startup option added for headless smoke (not in the
  original plan; enables future automated viewer runs).

NOT verified (no attended display on this box):
- Interactive loop: mouse clicks, 60 s hand-track, on-screen FPS stability.
- Qt backend: `cv2.imshow` needs `QT_QPA_PLATFORM=xcb` plus the full xcb
  dependency closure, which is not installed here (Wayland session, wheel
  ships xcb plugin but its deps are missing). Run on an attended machine
  with a working `import cv2; cv2.namedWindow` first.

To run attended: start `python -m sam2ws.server --port 8767`, then
`python -m sam2ws.viewer_client`. Left-click adds objects, right-click is
not bound in v1 (removal via restart) — removal UI is future work.
