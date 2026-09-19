from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, ValidationError

from protocol.validate import load_fixture, validate_instance
from protocol.ids import new_ulid
from protocol.stale import classify_hit
from protocol.uv import spec_uv_to_pca_viewport, spec_uv_to_pixel_center


class SchemaTests(unittest.TestCase):
    def test_valid_capture_envelope(self) -> None:
        data = load_fixture("valid", "capture_envelope.json")
        validate_instance("capture_envelope", data)

    def test_valid_hello_wrapper(self) -> None:
        data = load_fixture("valid", "hello.json")
        validate_instance("message", data)

    def test_valid_mark(self) -> None:
        data = load_fixture("valid", "scene_op_mark.json")
        validate_instance("scene_op", data)

    def test_valid_ack(self) -> None:
        data = load_fixture("valid", "placement_ack_placed.json")
        validate_instance("placement_ack", data)

    def test_model_must_not_emit_world_point(self) -> None:
        data = load_fixture("invalid", "model_world_point.json")
        with self.assertRaises(ValidationError):
            validate_instance("model_scene_op", data)

    def test_mark_without_target_rejected(self) -> None:
        data = load_fixture("invalid", "mark_missing_target.json")
        with self.assertRaises(ValidationError):
            validate_instance("scene_op", data)

    def test_ulid_lowercase_26(self) -> None:
        value = new_ulid()
        self.assertEqual(len(value), 26)
        self.assertEqual(value, value.lower())
        self.assertRegex(value, r"^[0-7][0-9a-hjkmnp-tv-z]{25}$")

    def test_schemas_are_valid_draft_2020_12(self) -> None:
        schemas = Path(__file__).resolve().parents[1] / "protocol" / "schemas"
        for name in ("message", "capture_envelope", "scene_op", "model_scene_op", "placement_ack"):
            with self.subTest(schema=name):
                schema = json.loads((schemas / f"{name}.json").read_text())
                self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
                Draft202012Validator.check_schema(schema)

    def test_message_rejects_invalid_wrapper(self) -> None:
        hello = load_fixture("valid", "hello.json")
        for field, value in (("v", 2), ("type", "unknown"), ("turn_id", -1), ("payload", [])):
            with self.subTest(field=field):
                data = copy.deepcopy(hello)
                data[field] = value
                with self.assertRaises(ValidationError):
                    validate_instance("message", data)
        for field in hello:
            with self.subTest(missing=field):
                data = copy.deepcopy(hello)
                del data[field]
                with self.assertRaises(ValidationError):
                    validate_instance("message", data)
        with self.assertRaises(ValidationError):
            validate_instance("message", {**hello, "extra": True})

    def test_capture_requires_all_envelope_fields(self) -> None:
        envelope = load_fixture("valid", "capture_envelope.json")
        for field in envelope:
            with self.subTest(missing=field):
                data = copy.deepcopy(envelope)
                del data[field]
                with self.assertRaises(ValidationError):
                    validate_instance("capture_envelope", data)

    def test_capture_allows_null_pointing_and_hint(self) -> None:
        data = load_fixture("valid", "capture_envelope.json")
        data.update(pointing=None, world_hint=None, capture_geometry_available=False)
        data["distortion"] = {"model": "brown_conrady", "k": [0.1, -0.2]}
        validate_instance("capture_envelope", data)

    def test_capture_rejects_invalid_nested_fields(self) -> None:
        envelope = load_fixture("valid", "capture_envelope.json")
        changes = (
            (("camera",), "right"),
            (("image_w",), 0),
            (("capture_geometry_available",), "true"),
            (("intrinsics", "fx"), "0"),
            (("distortion", "k"), ["bad"]),
            (("pose", "frame"), "world"),
            (("crop", "sx"), "1"),
            (("pointing", "source"), "gaze"),
            (("pointing", "direction", "z"), "1"),
            (("world_hint", "source"), "head"),
            (("world_hint", "frame"), "world"),
        )
        for path, value in changes:
            with self.subTest(path=path):
                data = copy.deepcopy(envelope)
                node = data
                for key in path[:-1]:
                    node = node[key]
                node[path[-1]] = value
                with self.assertRaises(ValidationError):
                    validate_instance("capture_envelope", data)
        for field in ("intrinsics", "distortion", "pose", "crop", "pointing", "world_hint"):
            for key in envelope[field]:
                with self.subTest(object=field, missing=key):
                    data = copy.deepcopy(envelope)
                    del data[field][key]
                    with self.assertRaises(ValidationError):
                        validate_instance("capture_envelope", data)

    def test_model_targets_and_kind_specific_fields(self) -> None:
        frame_id = load_fixture("valid", "capture_envelope.json")["frame_id"]
        drawing_id = load_fixture("valid", "placement_ack_placed.json")["drawing_id"]
        targets = (
            {"type": "capture_hint", "frame_id": frame_id},
            {"type": "image_point", "frame_id": frame_id, "u": 0.42, "v": 0.51},
            {"type": "image_box", "frame_id": frame_id, "u0": 0, "v0": 0, "u1": 1, "v1": 1},
            {"type": "drawing", "drawing_id": drawing_id},
            {"type": "pointing", "frame_id": frame_id},
        )
        for target in targets:
            with self.subTest(target=target["type"]):
                validate_instance("model_scene_op", {"kind": "mark", "target": target})
                for key in target:
                    invalid = dict(target)
                    del invalid[key]
                    with self.assertRaises(ValidationError):
                        validate_instance("model_scene_op", {"kind": "mark", "target": invalid})
                for key in ("px", "py", "pz", "qx", "frame"):
                    with self.assertRaises(ValidationError):
                        validate_instance("model_scene_op", {"kind": "mark", "target": {**target, key: 0}})
        for data in (
            {"kind": "label", "target": targets[0], "text": "Surface"},
            {"kind": "connect", "from": targets[0], "to": targets[1], "motion": {"kind": "travel", "period_s": 2}},
            {"kind": "remove", "target": targets[3]},
            {"kind": "undo"},
        ):
            with self.subTest(kind=data["kind"]):
                validate_instance("model_scene_op", data)

    def test_model_rejects_world_coordinates_in_connect_endpoints(self) -> None:
        world = load_fixture("invalid", "model_world_point.json")["target"]
        hint = load_fixture("valid", "scene_op_mark.json")["target"]
        for endpoint in ("from", "to"):
            data = {"kind": "connect", "from": hint, "to": hint}
            data[endpoint] = world
            with self.subTest(endpoint=endpoint), self.assertRaises(ValidationError):
                validate_instance("model_scene_op", data)

    def test_model_rejects_coordinator_metadata(self) -> None:
        mark = load_fixture("valid", "scene_op_mark.json")
        for field in ("op_id", "turn_id", "stage_epoch"):
            data = {"kind": "mark", "target": mark["target"], field: mark[field]}
            with self.subTest(field=field), self.assertRaises(ValidationError):
                validate_instance("model_scene_op", data)

    def test_scene_op_requires_metadata(self) -> None:
        mark = load_fixture("valid", "scene_op_mark.json")
        for field in ("op_id", "turn_id", "stage_epoch", "kind"):
            with self.subTest(field=field):
                data = copy.deepcopy(mark)
                del data[field]
                with self.assertRaises(ValidationError):
                    validate_instance("scene_op", data)

    def test_scene_op_accepts_quest_world_point(self) -> None:
        data = load_fixture("valid", "scene_op_mark.json")
        data["target"] = load_fixture("invalid", "model_world_point.json")["target"]
        validate_instance("scene_op", data)
        data["target"].update(nx=0, ny=1, nz=0, frame_id=load_fixture("valid", "capture_envelope.json")["frame_id"])
        validate_instance("scene_op", data)
        for field in ("px", "py", "pz", "frame"):
            invalid = copy.deepcopy(data)
            del invalid["target"][field]
            with self.subTest(field=field), self.assertRaises(ValidationError):
                validate_instance("scene_op", invalid)

    def test_mark_motion_can_be_omitted_or_null_without_mutation(self) -> None:
        data = load_fixture("valid", "scene_op_mark.json")
        del data["motion"]
        validate_instance("scene_op", data)
        self.assertNotIn("motion", data)
        data["motion"] = None
        validate_instance("scene_op", data)

    def test_ghost_requires_rotate_or_slide_motion(self) -> None:
        mark = load_fixture("valid", "scene_op_mark.json")
        for schema in ("model_scene_op", "scene_op"):
            data = {"kind": "ghost", "target": mark["target"]}
            if schema == "scene_op":
                data.update({key: mark[key] for key in ("op_id", "turn_id", "stage_epoch")})
            with self.subTest(schema=schema):
                with self.assertRaises(ValidationError):
                    validate_instance(schema, data)
                for motion in (None, {"kind": "pulse", "period_s": 1.2}, {"kind": "travel", "period_s": 2}):
                    with self.assertRaises(ValidationError):
                        validate_instance(schema, {**data, "motion": motion})
                for motion in (
                    {"kind": "rotate", "angle_deg": 90, "period_s": 2},
                    {"kind": "slide", "axis": "x", "distance_m": 0.1, "period_s": 2},
                ):
                    validate_instance(schema, {**data, "motion": motion})

    def test_invalid_targets_kinds_and_motion_bounds(self) -> None:
        hint = load_fixture("valid", "scene_op_mark.json")["target"]
        invalid_ops = (
            {"kind": "unknown"},
            {"kind": "mark"},
            {"kind": "mark", "target": {"type": "unknown"}},
            {"kind": "mark", "target": {"type": "image_point", "frame_id": hint["frame_id"], "u": -0.1, "v": 0}},
            {"kind": "mark", "target": {"type": "image_box", "frame_id": hint["frame_id"], "u0": 0, "v0": 0, "u1": 1.1, "v1": 1}},
            {"kind": "label", "target": hint},
            {"kind": "label", "target": hint, "text": "x" * 49},
            {"kind": "connect", "from": hint},
            {"kind": "remove", "target": hint},
            {"kind": "place_known", "target": hint, "asset_id": "demo", "scale": 2.1},
            {"kind": "mark", "target": hint, "shape": "cube"},
        )
        invalid_motions = (
            {"kind": "unknown"},
            {"kind": "pulse", "period_s": 0.5},
            {"kind": "pulse", "period_s": 3.1},
            {"kind": "travel", "period_s": 0.9},
            {"kind": "travel", "period_s": 4.1},
            {"kind": "rotate", "axis": "w", "angle_deg": 90, "period_s": 2},
            {"kind": "rotate", "angle_deg": 181, "period_s": 2},
            {"kind": "slide", "axis": "y", "distance_m": 0.1, "period_s": 2},
            {"kind": "slide", "axis": "x", "distance_m": 0.02, "period_s": 2},
        )
        for data in invalid_ops + tuple({"kind": "mark", "target": hint, "motion": motion} for motion in invalid_motions):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                validate_instance("model_scene_op", data)

    def test_ack_status_conditions(self) -> None:
        placed = load_fixture("valid", "placement_ack_placed.json")
        for field in ("drawing_id", "pin"):
            for value in (None, "invalid"):
                with self.subTest(status="placed", field=field, value=value):
                    with self.assertRaises(ValidationError):
                        validate_instance("placement_ack", {**placed, field: value})
            data = dict(placed)
            del data[field]
            with self.assertRaises(ValidationError):
                validate_instance("placement_ack", data)
        for status in ("rejected", "stale"):
            for reason in ("out_of_camera", "too_small", "too_close", "no_surface", "clutter", "invalid", "superseded", "timeout"):
                data = {**placed, "status": status, "drawing_id": None, "pin": None, "reason": reason}
                with self.subTest(status=status, reason=reason):
                    validate_instance("placement_ack", data)
            for field, value in (("drawing_id", placed["drawing_id"]), ("reason", None), ("reason", "unknown")):
                with self.assertRaises(ValidationError):
                    validate_instance("placement_ack", {**data, field: value})
            for field in ("reason", "drawing_id"):
                invalid = dict(data)
                del invalid[field]
                with self.assertRaises(ValidationError):
                    validate_instance("placement_ack", invalid)
        for drawing_id in (placed["drawing_id"], None):
            validate_instance("placement_ack", {**placed, "status": "applied", "drawing_id": drawing_id, "pin": None})
        with self.assertRaises(ValidationError):
            validate_instance("placement_ack", {**placed, "status": "unknown"})

    def test_protocol_ids_are_lowercase_ulids(self) -> None:
        for schema, fixture, field in (
            ("capture_envelope", "capture_envelope.json", "frame_id"),
            ("scene_op", "scene_op_mark.json", "op_id"),
            ("placement_ack", "placement_ack_placed.json", "drawing_id"),
        ):
            valid = load_fixture("valid", fixture)
            for value in (valid[field].upper(), "01k...", "z" * 26, "i" * 26):
                with self.subTest(schema=schema, value=value), self.assertRaises(ValidationError):
                    validate_instance(schema, {**valid, field: value})

    def test_inspect_objects_valid_and_rejects_mask(self) -> None:
        data = load_fixture("valid", "inspect_objects.json")
        validate_instance("inspect_objects", data)
        with self.assertRaises(ValidationError):
            validate_instance("inspect_objects", {**data, "mask_png_b64": "xxxx"})

    def test_inspect_result_has_boxes_not_masks(self) -> None:
        data = load_fixture("valid", "inspect_objects_result.json")
        validate_instance("inspect_objects_result", data)
        self.assertNotIn("mask", data)
        self.assertNotIn("mask_png_b64", data["objects"][0])

    def test_start_generation_valid(self) -> None:
        validate_instance("start_generation", load_fixture("valid", "start_generation.json"))
        validate_instance(
            "start_generation_result",
            load_fixture("valid", "start_generation_result.json"),
        )

    def test_model_must_not_emit_place_generated_or_place_known(self) -> None:
        target = load_fixture("valid", "scene_op_mark.json")["target"]
        for kind in ("place_generated", "place_known"):
            with self.subTest(kind=kind), self.assertRaises(ValidationError):
                validate_instance("model_scene_op", {"kind": kind, "target": target})


class StaleTests(unittest.TestCase):
    def test_same_ray_far_is_stale(self) -> None:
        cached = (0.0, 0.8, 1.0)
        delayed = (0.2, 0.8, 1.0)
        self.assertEqual(classify_hit(cached, delayed, same_ray=True), "stale")

    def test_same_ray_close_is_placed(self) -> None:
        cached = (0.0, 0.8, 1.0)
        delayed = (0.05, 0.8, 1.0)
        self.assertEqual(classify_hit(cached, delayed, same_ray=True), "placed")

    def test_different_ray_ignores_centre_hint(self) -> None:
        centre = (0.0, 0.8, 1.0)
        other = (0.5, 0.8, 1.2)
        self.assertEqual(classify_hit(centre, other, same_ray=False), "placed")

    def test_no_delayed_hit(self) -> None:
        for cached in ((0.0, 0.8, 1.0), None):
            for same_ray in (True, False):
                with self.subTest(cached=cached, same_ray=same_ray):
                    self.assertEqual(classify_hit(cached, None, same_ray), "no_surface")

    def test_no_cache_delayed_only_is_stale(self) -> None:
        self.assertEqual(classify_hit(None, (0.0, 0.8, 1.0), True), "stale")

    def test_different_ray_without_cache_is_placed(self) -> None:
        self.assertEqual(classify_hit(None, (0.0, 0.8, 1.0), False), "placed")

    def test_same_ray_identical_hit_is_placed(self) -> None:
        hit = (0.0, 0.8, 1.0)
        self.assertEqual(classify_hit(hit, hit, True), "placed")

    def test_same_ray_at_threshold_is_placed(self) -> None:
        self.assertEqual(classify_hit((0.0, 0.0, 0.0), (0.12, 0.0, 0.0), True), "placed")

    def test_same_ray_just_over_threshold_is_stale(self) -> None:
        self.assertEqual(classify_hit((0.0, 0.0, 0.0), (0.120001, 0.0, 0.0), True), "stale")

    def test_same_ray_uses_euclidean_distance_in_all_axes(self) -> None:
        cached = (0.0, 0.0, 0.0)
        for delayed in ((0.0, 0.13, 0.0), (0.0, 0.0, -0.13), (0.08, 0.08, 0.08)):
            with self.subTest(delayed=delayed):
                self.assertEqual(classify_hit(cached, delayed, True), "stale")
        self.assertEqual(classify_hit(cached, (0.06, -0.06, 0.06), True), "placed")

    def test_same_ray_uses_custom_threshold(self) -> None:
        cached = (0.0, 0.0, 0.0)
        delayed = (0.2, 0.0, 0.0)
        self.assertEqual(classify_hit(cached, delayed, True, threshold_m=0.25), "placed")
        self.assertEqual(classify_hit(cached, delayed, True, threshold_m=0.1), "stale")


class UvTests(unittest.TestCase):
    def setUp(self) -> None:
        path = Path(__file__).resolve().parents[1] / "protocol" / "fixtures" / "uv_cases.json"
        self.cases = json.loads(path.read_text(encoding="utf-8"))

    def test_shared_fixture_contract(self) -> None:
        self.assertIsInstance(self.cases, list)
        self.assertEqual(len(self.cases), 3)
        for case in self.cases:
            with self.subTest(case=case):
                self.assertEqual(
                    set(case),
                    {"u", "v", "sent_w", "sent_h", "pca_viewport", "pixel_center"},
                )
                for field in ("pca_viewport", "pixel_center"):
                    self.assertIsInstance(case[field], list)
                    self.assertEqual(len(case[field]), 2)
        inputs = {(case["u"], case["v"]) for case in self.cases}
        self.assertIn((0.0, 0.0), inputs)
        self.assertIn((1.0, 1.0), inputs)
        self.assertTrue(any(
            0 < case["u"] < 1 and 0 < case["v"] < 1
            and case["sent_w"] == 1280 and case["sent_h"] == 960
            for case in self.cases
        ))

    def test_spec_uv_to_pca_viewport(self) -> None:
        for case in self.cases:
            with self.subTest(u=case["u"], v=case["v"]):
                x, y = spec_uv_to_pca_viewport(case["u"], case["v"])
                self.assertAlmostEqual(x, case["pca_viewport"][0])
                self.assertAlmostEqual(y, case["pca_viewport"][1])

    def test_spec_uv_to_pixel_center(self) -> None:
        for case in self.cases:
            with self.subTest(u=case["u"], v=case["v"]):
                x, y = spec_uv_to_pixel_center(
                    case["u"], case["v"], case["sent_w"], case["sent_h"]
                )
                self.assertAlmostEqual(x, case["pixel_center"][0])
                self.assertAlmostEqual(y, case["pixel_center"][1])
