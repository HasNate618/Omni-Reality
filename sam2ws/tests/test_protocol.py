import unittest


class ProtocolTest(unittest.TestCase):
    def test_round_trip_and_reject_oversize(self):
        from sam2ws import protocol
        req = protocol.build_request(
            session_id="01k0000000000000000000000",
            frame_id=7,
            t_unix_ns=123,
            w=640, h=480,
            jpeg_bytes=b"\xff\xd8" + b"0" * 100,
            clicks=[{"x": 10, "y": 20, "label": 1, "obj_id": 1}],
            remove={"obj_ids": [], "all": False},
        )
        self.assertEqual(protocol.validate_request(req)["frame_id"], 7)
        import copy
        bad = copy.deepcopy(req)
        bad["image"]["jpeg_b64"] = "!!!not-base64!!!"
        with self.assertRaises(protocol.ProtocolError):
            protocol.validate_request(bad)
        with self.assertRaises(protocol.ProtocolError):
            protocol.validate_request({"v": 999})
