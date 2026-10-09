"""J14 finite framed-result transport proof."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import sys
import unittest

import rfc8785


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.framed_result_transport import (  # noqa: E402
    FramedResultConfig,
    FramedResultReceiver,
    FramedResultResumeRequest,
    FramedResultTransferStore,
    FramedResultTransportError,
    frame_complete_result,
)


class FramedResultTransportV1Tests(unittest.TestCase):
    def test_frames_reassemble_only_after_every_unique_verified_chunk(self) -> None:
        result = {"z": ["é", 2], "a": {"second": True, "first": None}}
        frames = frame_complete_result(
            logical_result_identity="market-result-v1:sha256:" + "a" * 64,
            result=result,
            config=FramedResultConfig(frame_payload_bytes=8),
        )
        receiver = FramedResultReceiver()

        expected_payload_sha256 = hashlib.sha256(rfc8785.dumps(result)).hexdigest()
        expected_transfer_id = hashlib.sha256(
            rfc8785.dumps(
                {
                    "logical_result_identity": "market-result-v1:sha256:" + "a" * 64,
                    "message_family": "j14-framed-result-v1",
                    "payload_sha256": expected_payload_sha256,
                    "schema_version": "j14-framed-result-v1",
                }
            )
        ).hexdigest()
        self.assertEqual(expected_payload_sha256, frames[0].payload_sha256)
        self.assertEqual(expected_transfer_id, frames[0].transfer_id)

        for frame in reversed(frames[:-1]):
            self.assertIsNone(receiver.receive(frame.message()))
        self.assertIsNone(receiver.receive(frames[0]))
        self.assertEqual(result, receiver.receive(frames[-1]))

    def test_receiver_refuses_tampered_chunk_or_transfer_metadata(self) -> None:
        frames = frame_complete_result(
            logical_result_identity="result:1",
            result={"payload": "abcdefgh"},
            config=FramedResultConfig(frame_payload_bytes=4),
        )
        tampered_payload = replace(frames[0], chunk_payload=b"xxxx")
        with self.assertRaisesRegex(FramedResultTransportError, "integrity_failure"):
            FramedResultReceiver().receive(tampered_payload)

        tampered_identity = replace(frames[0], logical_result_identity="result:2")
        with self.assertRaisesRegex(FramedResultTransportError, "integrity_failure"):
            FramedResultReceiver().receive(tampered_identity)

        modified_payload = b'{"pb'
        modified_frame = replace(
            frames[0],
            chunk_payload=modified_payload,
            chunk_sha256=hashlib.sha256(modified_payload).hexdigest(),
        )
        receiver = FramedResultReceiver()
        for frame in frames[1:]:
            self.assertIsNone(receiver.receive(frame))
        with self.assertRaisesRegex(FramedResultTransportError, "integrity_failure"):
            receiver.receive(modified_frame)

    def test_resume_returns_exact_retained_frames_and_expiry_is_typed(self) -> None:
        now = [0.0]
        store = FramedResultTransferStore(
            FramedResultConfig(frame_payload_bytes=4, retention_seconds=5),
            clock=lambda: now[0],
        )
        frames = store.admit(logical_result_identity="result:1", result={"payload": "abcdefgh"})

        request = FramedResultResumeRequest.from_message(
            FramedResultResumeRequest(frames[0].transfer_id, 1).message()
        )
        resumed = store.resume(request)
        self.assertEqual(frames[1:], resumed)

        with self.assertRaisesRegex(FramedResultTransportError, "integrity_failure"):
            FramedResultResumeRequest.from_message({"message_type": "resume"})

        now[0] = 5.0
        with self.assertRaisesRegex(FramedResultTransportError, "transfer_expired"):
            store.resume(FramedResultResumeRequest(frames[0].transfer_id, 1))

    def test_admission_refuses_beyond_declared_retention_bounds(self) -> None:
        store = FramedResultTransferStore(
            FramedResultConfig(
                frame_payload_bytes=4,
                max_retained_payload_bytes=8,
                max_in_flight_transfers=1,
            )
        )
        with self.assertRaisesRegex(FramedResultTransportError, "transfer_refused"):
            store.admit(logical_result_identity="result:1", result={"payload": "abcdefgh"})

        admitted = FramedResultTransferStore(
            FramedResultConfig(frame_payload_bytes=4, max_retained_payload_bytes=128, max_in_flight_transfers=1)
        )
        admitted.admit(logical_result_identity="result:1", result={"payload": "a"})
        with self.assertRaisesRegex(FramedResultTransportError, "transfer_refused"):
            admitted.admit(logical_result_identity="result:2", result={"payload": "b"})


if __name__ == "__main__":
    unittest.main()
