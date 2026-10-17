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

    def test_an_owned_transfer_resumes_only_for_its_owner_and_otherwise_looks_expired(self) -> None:
        # #340: a transfer admitted for a scope is retrievable under that scope
        # only, and saying "expired" for a mismatch confirms nothing.
        store = FramedResultTransferStore(FramedResultConfig(frame_payload_bytes=4))
        frames = store.admit(
            logical_result_identity="result:1", result={"payload": "abcdefgh"}, owner="j02.jobs.read",
        )
        request = FramedResultResumeRequest(frames[0].transfer_id, 0)
        self.assertEqual(("j02.jobs.read",), store.retained_owner(frames[0].transfer_id))
        self.assertEqual(frames, store.resume(request, owner="j02.jobs.read"))
        for wrong in (None, "j02.admitted_input.read", "j02.jobs.submit"):
            with self.subTest(owner=wrong):
                with self.assertRaisesRegex(FramedResultTransportError, "transfer_expired"):
                    store.resume(request, owner=wrong)
        self.assertIsNone(store.retained_owner("0" * 64))

    def test_the_same_bytes_cannot_be_readmitted_for_another_owner(self) -> None:
        store = FramedResultTransferStore(FramedResultConfig(frame_payload_bytes=4))
        store.admit(logical_result_identity="result:1", result={"p": "abcdefgh"}, owner="a")
        self.assertEqual(
            store.admit(logical_result_identity="result:1", result={"p": "abcdefgh"}, owner="a"),
            store.admit(logical_result_identity="result:1", result={"p": "abcdefgh"}, owner="a"),
        )
        with self.assertRaisesRegex(FramedResultTransportError, "transfer_refused"):
            store.admit(logical_result_identity="result:1", result={"p": "abcdefgh"}, owner="b")
        with self.assertRaisesRegex(FramedResultTransportError, "transfer_refused"):
            store.admit(logical_result_identity="result:1", result={"p": "abcdefgh"})
        with self.assertRaises(ValueError):
            store.admit(logical_result_identity="result:2", result={"p": "x"}, owner="")

    def test_a_batch_is_admitted_whole_or_not_at_all(self) -> None:
        # #341: a caller that needs several transfers together must never leave a
        # partial, resumable set behind when the store refuses part of it.
        config = FramedResultConfig(frame_payload_bytes=4, max_in_flight_transfers=3)
        store = FramedResultTransferStore(config)
        existing = store.admit(logical_result_identity="result:0", result={"p": "abcdefgh"}, owner="a")
        before = dict(store._transfers)
        batch = [(f"result:{n}", {"p": "abcdefgh", "n": n}) for n in (1, 2, 3)]
        with self.assertRaisesRegex(FramedResultTransportError, "transfer_refused"):
            store.admit_many(batch, owner="a")  # three new ones, two slots left
        self.assertEqual(before, dict(store._transfers))  # untouched, existing one kept
        self.assertEqual(existing, store.admit(
            logical_result_identity="result:0", result={"p": "abcdefgh"}, owner="a",
        ))

        admitted = store.admit_many(batch[:2], owner="a")
        self.assertEqual(
            [frames[0].logical_result_identity for frames in admitted], ["result:1", "result:2"],
        )  # in the order given
        self.assertEqual(3, len(store._transfers))

    def test_a_batch_reuses_identical_transfers_and_counts_them_once(self) -> None:
        store = FramedResultTransferStore(
            FramedResultConfig(frame_payload_bytes=4, max_in_flight_transfers=2)
        )
        first = store.admit(logical_result_identity="result:1", result={"p": "abcdefgh"}, owner="a")
        admitted = store.admit_many(
            [("result:1", {"p": "abcdefgh"}), ("result:2", {"p": "x"}), ("result:2", {"p": "x"})],
            owner="a",
        )
        self.assertEqual(first, admitted[0])
        self.assertEqual(admitted[1], admitted[2])
        self.assertEqual(2, len(store._transfers))

    def test_a_batch_naming_another_owners_transfer_admits_nothing(self) -> None:
        store = FramedResultTransferStore(FramedResultConfig(frame_payload_bytes=4))
        store.admit(logical_result_identity="result:1", result={"p": "abcdefgh"}, owner="b")
        before = dict(store._transfers)
        with self.assertRaisesRegex(FramedResultTransportError, "transfer_refused"):
            store.admit_many(
                [("result:2", {"p": "x"}), ("result:1", {"p": "abcdefgh"})], owner="a",
            )
        self.assertEqual(before, dict(store._transfers))
        with self.assertRaises(ValueError):
            store.admit_many([("result:3", {"p": "x"})], owner="")

    def test_a_reservation_is_invisible_until_committed_and_released_cleanly(self) -> None:
        store = FramedResultTransferStore(
            FramedResultConfig(frame_payload_bytes=4, max_in_flight_transfers=4)
        )
        items = [("result:1", {"p": "abcdefgh"}), ("result:2", {"p": "ijklmnop"})]
        reservation = store.reserve_many(items, owner="a")
        transfer_id = reservation.frames[0][0].transfer_id
        # Capacity is taken, but nothing is retained for anyone to resume.
        self.assertEqual(2, len(store._transfers))
        self.assertIsNone(store.retained_owner(transfer_id))
        with self.assertRaisesRegex(FramedResultTransportError, "transfer_expired"):
            store.resume(FramedResultResumeRequest(transfer_id, 0), owner="a")
        reservation.release()
        self.assertEqual({}, dict(store._transfers))
        self.assertEqual({}, dict(store._pending))
        reservation.release()  # idempotent

        committed = store.reserve_many(items, owner="a")
        committed.commit()
        self.assertEqual(("a",), store.retained_owner(transfer_id))
        self.assertEqual(committed.frames[0], store.resume(
            FramedResultResumeRequest(transfer_id, 0), owner="a"))
        committed.release()  # a no-op once committed: published work is not withdrawn
        self.assertEqual(2, len(store._transfers))

    def test_releasing_a_reservation_never_removes_what_is_not_solely_its_own(self) -> None:
        store = FramedResultTransferStore(FramedResultConfig(frame_payload_bytes=4))
        existing = store.admit(logical_result_identity="result:0", result={"p": "abcdefgh"}, owner="a")
        mine = store.reserve_many(
            [("result:0", {"p": "abcdefgh"}), ("result:1", {"p": "ijklmnop"})], owner="a",
        )
        other = store.reserve_many([("result:1", {"p": "ijklmnop"})], owner="a")
        shared = mine.frames[1][0].transfer_id
        mine.release()
        # The committed one is untouched, and the one the other still holds stays.
        self.assertEqual(existing, store.resume(
            FramedResultResumeRequest(existing[0].transfer_id, 0), owner="a"))
        self.assertIn(shared, store._transfers)
        other.commit()
        self.assertEqual(("a",), store.retained_owner(shared))
        # The last holder going away is what removes a provisional transfer.
        solo = store.reserve_many([("result:2", {"p": "qrstuvwx"})], owner="a")
        solo_id = solo.frames[0][0].transfer_id
        solo.release()
        self.assertNotIn(solo_id, store._transfers)

    def test_an_expired_provisional_transfer_is_forgotten(self) -> None:
        now = [0.0]
        store = FramedResultTransferStore(
            FramedResultConfig(frame_payload_bytes=4, retention_seconds=10),
            clock=lambda: now[0],
        )
        reservation = store.reserve_many([("result:1", {"p": "abcdefgh"})], owner="a")
        now[0] = 11.0
        self.assertIsNone(store.retained_owner(reservation.frames[0][0].transfer_id))
        self.assertEqual({}, dict(store._transfers))
        self.assertEqual({}, dict(store._pending))
        reservation.release()  # still safe

    def test_a_stale_reservation_cannot_publish_a_later_copy_of_the_same_transfer(self) -> None:
        now = [0.0]
        store = FramedResultTransferStore(
            FramedResultConfig(frame_payload_bytes=4, retention_seconds=10),
            clock=lambda: now[0],
        )
        first = ("result:1", {"p": "abcdefgh"})
        second = ("result:2", {"p": "ijklmnop"})
        stale = store.reserve_many([first], owner="a")
        transfer_id = stale.frames[0][0].transfer_id
        now[0] = 11.0  # the stale reservation expires
        fresh = store.reserve_many([first, second], owner="a")  # recreates the same id
        second_id = fresh.frames[1][0].transfer_id

        with self.assertRaisesRegex(FramedResultTransportError, "transfer_expired"):
            stale.commit()
        # Nothing of the new reservation was published by the stale one.
        self.assertIsNone(store.retained_owner(transfer_id))
        self.assertIsNone(store.retained_owner(second_id))
        stale.release()  # and it withdraws nothing of the new copy either
        self.assertIn(transfer_id, store._transfers)
        fresh.release()
        self.assertEqual({}, dict(store._transfers))
        self.assertEqual({}, dict(store._pending))

        # The same, when the stale reservation had only reused a committed copy.
        committed = store.admit(logical_result_identity="result:1", result=first[1], owner="a")
        holder = store.reserve_many([first], owner="a")
        now[0] = 100.0  # the committed copy expires under it
        replacement = store.reserve_many([first, second], owner="a")
        with self.assertRaisesRegex(FramedResultTransportError, "transfer_expired"):
            holder.commit()
        self.assertIsNone(store.retained_owner(committed[0].transfer_id))
        replacement.commit()
        self.assertEqual(("a",), store.retained_owner(committed[0].transfer_id))
        self.assertEqual(("a",), store.retained_owner(second_id))

    def test_a_reservation_whose_copy_was_replaced_and_published_elsewhere_fails_too(self) -> None:
        now = [0.0]
        store = FramedResultTransferStore(
            FramedResultConfig(frame_payload_bytes=4, retention_seconds=10),
            clock=lambda: now[0],
        )
        item = ("result:1", {"p": "abcdefgh"})
        stale = store.reserve_many([item], owner="a")
        now[0] = 11.0
        # The same bytes are admitted again, and committed, by someone else: a
        # valid transfer, but not the copy this reservation was holding.
        replacement = store.admit(logical_result_identity=item[0], result=item[1], owner="a")
        with self.assertRaisesRegex(FramedResultTransportError, "transfer_expired"):
            stale.commit()
        self.assertEqual(replacement, store.resume(
            FramedResultResumeRequest(replacement[0].transfer_id, 0), owner="a"))
        stale.release()  # and it removes nothing of the replacement
        self.assertEqual(("a",), store.retained_owner(replacement[0].transfer_id))

    def test_a_commit_is_all_or_nothing_and_retention_runs_from_publication(self) -> None:
        now = [0.0]
        store = FramedResultTransferStore(
            FramedResultConfig(frame_payload_bytes=4, retention_seconds=10),
            clock=lambda: now[0],
        )
        reservation = store.reserve_many(
            [("result:1", {"p": "abcdefgh"}), ("result:2", {"p": "ijklmnop"})], owner="a",
        )
        ids = [frames[0].transfer_id for frames in reservation.frames]
        now[0] = 9.0
        reservation.commit()  # still inside its own reservation window
        now[0] = 15.0  # past the reservation's expiry, inside the published one
        self.assertEqual(("a",), store.retained_owner(ids[0]))
        self.assertEqual(("a",), store.retained_owner(ids[1]))
        now[0] = 19.5
        self.assertIsNone(store.retained_owner(ids[0]))

        now[0] = 100.0
        partial = store.reserve_many(
            [("result:3", {"p": "qrstuvwx"}), ("result:4", {"p": "yzabcdef"})], owner="a",
        )
        # One of the two is lost before the commit: neither may be published.
        lost = partial.frames[1][0].transfer_id
        del store._transfers[lost]
        del store._pending[lost]
        del store._generations[lost]
        kept = partial.frames[0][0].transfer_id
        with self.assertRaisesRegex(FramedResultTransportError, "transfer_expired"):
            partial.commit()
        self.assertIsNone(store.retained_owner(kept))
        self.assertIn(kept, store._pending)  # still provisional, not published
        partial.release()
        self.assertEqual({}, dict(store._transfers))

    def test_an_admission_whose_commit_fails_releases_everything_it_reserved(self) -> None:
        class Racy(FramedResultTransferStore):
            """Expires the reused member between the reservation and the commit."""

            def __init__(self, *args, always, **kwargs):
                super().__init__(*args, **kwargs)
                self.always = always
                self.fired = 0
                self.commits = 0

            def _commit(self, lease, transfer_ids, seen, held, *rest):
                self.commits += 1
                if self.fired == 0 or self.always:
                    self.fired += 1
                    for transfer_id in transfer_ids:
                        # The first attempt loses a reused, committed member; a
                        # persistent failure loses the first member every time.
                        if transfer_id not in held or (self.always and transfer_id == transfer_ids[0]):
                            # exactly what the store's own expiry removes
                            self._transfers.pop(transfer_id, None)
                            self._generations.pop(transfer_id, None)
                            self._pending.pop(transfer_id, None)
                return super()._commit(lease, transfer_ids, seen, held, *rest)

        config = FramedResultConfig(
            frame_payload_bytes=4, max_in_flight_transfers=3, max_retained_payload_bytes=64,
        )
        reused = ("result:0", {"p": "abcdefgh"})
        batch = [reused, ("result:1", {"p": "ijklmnop"}), ("result:2", {"p": "qrstuvwx"})]

        # The expiry is transient: the batch is admitted whole on the next try.
        store = Racy(config, always=False)
        store.admit(logical_result_identity=reused[0], result=reused[1], owner="a")
        store.fired = store.commits = 0  # the set-up admission must not use the loss up
        admitted = store.admit_many(batch, owner="a")
        self.assertEqual(2, store.commits)  # the first commit lost a member, the retry won
        self.assertEqual(1, store.fired)
        self.assertEqual(3, len(admitted))
        self.assertEqual({}, dict(store._pending))
        for frames in admitted:
            self.assertEqual(("a",), store.retained_owner(frames[0].transfer_id))

        # When it keeps failing, nothing of it stays held: no capacity, no slot.
        store = Racy(config, always=False)
        store.admit(logical_result_identity=reused[0], result=reused[1], owner="a")
        store.always = True
        store.fired = store.commits = 0
        with self.assertRaisesRegex(FramedResultTransportError, "transfer_refused"):
            store.admit_many(batch, owner="a")
        self.assertEqual({}, dict(store._pending))
        self.assertEqual({}, dict(store._transfers))
        self.assertEqual({}, dict(store._generations))
        self.assertEqual(0, store._retained_payload_bytes)
        # The next batch is not refused for capacity that nobody holds.
        store.always = False
        store.fired = 1
        self.assertEqual(3, len(store.admit_many(batch, owner="a")))

    def test_an_unowned_transfer_keeps_its_previous_behavior(self) -> None:
        store = FramedResultTransferStore(FramedResultConfig(frame_payload_bytes=4))
        frames = store.admit(logical_result_identity="result:1", result={"payload": "abcdefgh"})
        self.assertEqual((None,), store.retained_owner(frames[0].transfer_id))
        self.assertEqual(frames[1:], store.resume(FramedResultResumeRequest(frames[0].transfer_id, 1)))
        self.assertEqual(4, store.config.frame_payload_bytes)

    def test_concurrent_admission_and_resume_are_consistent(self) -> None:
        import threading

        store = FramedResultTransferStore(
            FramedResultConfig(frame_payload_bytes=8, max_in_flight_transfers=64)
        )
        errors = []

        def work(index):
            try:
                frames = store.admit(
                    logical_result_identity=f"result:{index % 8}", result={"n": index % 8, "p": "x" * 40},
                    owner="scope",
                )
                for frame in frames:
                    store.resume(FramedResultResumeRequest(frame.transfer_id, 0), owner="scope")
            except BaseException as error:  # noqa: BLE001
                errors.append(error)

        threads = [threading.Thread(target=work, args=(n,)) for n in range(64)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual([], errors)

    # -- #357: the sensitivity gaps of the audit and the owner's gate -----------------

    def test_an_expired_hold_leaves_no_generation_it_could_publish_against(self) -> None:
        now = [0.0]
        store = FramedResultTransferStore(
            FramedResultConfig(frame_payload_bytes=4, retention_seconds=10), clock=lambda: now[0],
        )
        reservation = store.reserve_many([("result:1", {"p": "abcdefgh"})], owner="a")
        self.assertEqual(1, len(store._generations))
        now[0] = 11.0
        store.retained_owner("0" * 64)  # any access runs the expiry
        self.assertEqual({}, dict(store._transfers))
        self.assertEqual({}, dict(store._generations))  # the cleanup, not only the copy
        with self.assertRaises(FramedResultTransportError) as caught:
            reservation.commit()
        self.assertEqual("transfer_expired", caught.exception.outcome)
        # Nothing was published by the failed commit, and the identifier is free.
        self.assertEqual({}, dict(store._transfers))
        self.assertIsNone(store.retained_owner(reservation.frames[0][0].transfer_id))

    def test_capacity_is_enforced_under_the_store_lock(self) -> None:
        import threading

        # A clock that makes two threads meet *between* the capacity check and
        # the insertion: without the store's lock both pass the check with one
        # free slot. With the lock the second thread cannot even start, the
        # barrier times out for the first, and exactly one batch is admitted.
        barrier = threading.Barrier(2, timeout=0.3)

        def meeting_clock() -> float:
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass
            return 0.0

        store = FramedResultTransferStore(
            FramedResultConfig(frame_payload_bytes=8, max_in_flight_transfers=1),
            clock=meeting_clock,
        )
        outcomes: list[str] = []

        def reserve(index: int) -> None:
            try:
                store.reserve_many([(f"result:{index}", {"n": index})], owner="a")
                outcomes.append("held")
            except FramedResultTransportError as error:
                outcomes.append(error.outcome)

        threads = [threading.Thread(target=reserve, args=(n,)) for n in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(["held", "transfer_refused"], sorted(outcomes))
        self.assertEqual(1, len(store._transfers))

    def test_an_owners_gate_is_consulted_on_every_resume_and_fails_closed(self) -> None:
        answers = {"value": True}
        calls = []

        def gate():
            calls.append(True)
            if answers["value"] == "raises":
                raise RuntimeError("down")
            return answers["value"]

        store = FramedResultTransferStore(FramedResultConfig(frame_payload_bytes=4))
        reservation = store.reserve_many([("result:1", {"p": "abcdefgh"})], owner="a", gate=gate)
        reservation.commit()
        request = FramedResultResumeRequest(reservation.frames[0][0].transfer_id, 0)
        self.assertEqual(reservation.frames[0], store.resume(request, owner="a"))
        self.assertEqual(1, len(calls))
        for answer in (False, None, "yes", 1, "raises"):
            answers["value"] = answer
            with self.subTest(answer=answer), self.assertRaises(FramedResultTransportError) as caught:
                store.resume(request, owner="a")
            self.assertEqual("transfer_expired", caught.exception.outcome)
        answers["value"] = True  # a gate is a question, not a withdrawal
        self.assertEqual(reservation.frames[0], store.resume(request, owner="a"))
        with self.assertRaises(TypeError):
            store.reserve_many([("result:2", {"p": "x"})], owner="a", gate="not callable")

    def test_the_gate_runs_outside_the_store_lock(self) -> None:
        import threading

        store = FramedResultTransferStore(FramedResultConfig(frame_payload_bytes=4))
        seen: list[bool] = []
        holder: dict[str, str] = {}

        def gate() -> bool:
            # Another thread uses the store while the gate (which may do I/O) runs.
            worker = threading.Thread(
                target=lambda: seen.append(store.retained_owner(holder["id"]) is not None),
            )
            worker.start()
            worker.join(timeout=2.0)
            return not worker.is_alive()

        reservation = store.reserve_many([("result:1", {"p": "abcdefgh"})], owner="a", gate=gate)
        reservation.commit()
        holder["id"] = reservation.frames[0][0].transfer_id
        frames = store.resume(FramedResultResumeRequest(holder["id"], 0), owner="a")
        self.assertEqual(reservation.frames[0], frames)
        self.assertEqual([True], seen)

    def test_a_reused_transfer_keeps_the_gate_it_was_created_with(self) -> None:
        store = FramedResultTransferStore(FramedResultConfig(frame_payload_bytes=4))
        first = store.reserve_many([("result:1", {"p": "abcdefgh"})], owner="a", gate=lambda: False)
        first.commit()
        # The same bytes again, with a permissive gate: the retained copy is reused.
        second = store.reserve_many([("result:1", {"p": "abcdefgh"})], owner="a", gate=lambda: True)
        second.commit()
        with self.assertRaises(FramedResultTransportError):
            store.resume(FramedResultResumeRequest(first.frames[0][0].transfer_id, 0), owner="a")

    def test_a_publisher_can_withdraw_only_what_it_alone_created(self) -> None:
        store = FramedResultTransferStore(FramedResultConfig(frame_payload_bytes=4))
        item = ("result:1", {"p": "abcdefgh"})
        mine = store.reserve_many([item], owner="a")
        mine.commit()
        transfer_id = mine.frames[0][0].transfer_id
        mine.withdraw()  # published by this reservation alone: gone
        self.assertEqual({}, dict(store._transfers))
        self.assertEqual({}, dict(store._generations))
        self.assertEqual({}, dict(store._participants))
        self.assertIsNone(store.retained_owner(transfer_id))

        # Another reservation reused the copy after it was created: not withdrawn.
        first = store.reserve_many([item], owner="a")
        first.commit()
        second = store.reserve_many([item], owner="a")
        second.commit()
        first.withdraw()
        self.assertEqual((("a"),), store.retained_owner(transfer_id))
        # A copy this reservation only reused is never its to withdraw.
        third = store.reserve_many([item], owner="a")
        third.commit()
        third.withdraw()
        self.assertEqual((("a"),), store.retained_owner(transfer_id))

        # Before commit it is a release: a hold that was alone is removed, and it
        # is safe to call again.
        held = store.reserve_many([("result:2", {"p": "ijklmnop"})], owner="a")
        held.withdraw()
        held.withdraw()
        self.assertEqual(1, len(store._transfers))

    def test_the_publisher_of_a_copy_answers_for_it_not_its_creator(self) -> None:
        store = FramedResultTransferStore(FramedResultConfig(frame_payload_bytes=4))
        item = ("result:1", {"p": "abcdefgh"})
        creator = store.reserve_many([item], owner="a", gate=lambda: False)
        publisher = store.reserve_many([item], owner="a", gate=lambda: True)  # holds the same copy
        publisher.commit()
        creator.release()  # the creator lost: it must not leave its gate on the winner's copy
        transfer_id = publisher.frames[0][0].transfer_id
        frames = store.resume(FramedResultResumeRequest(transfer_id, 0), owner="a")
        self.assertEqual(publisher.frames[0], frames)

    def test_deliveries_that_all_fail_free_the_capacity_they_held(self) -> None:
        store = FramedResultTransferStore(
            FramedResultConfig(frame_payload_bytes=4, max_in_flight_transfers=1)
        )
        item = ("result:A", {"p": "data"})
        first = store.reserve_many([item], owner="x", gate=lambda: False)
        second = store.reserve_many([item], owner="x", gate=lambda: False)  # sees the same copy
        first.commit()
        second.withdraw()  # still undecided: the first one is
        self.assertEqual(1, len(store._transfers))
        first.withdraw()  # the last one to be undecided: nothing keeps the copy
        self.assertEqual({}, dict(store._transfers))
        self.assertEqual({}, dict(store._participants))
        store.reserve_many([("result:B", {"p": "other"})], owner="x").release()  # no transfer_refused

    def test_a_copy_that_one_delivery_confirmed_survives_every_other_failure(self) -> None:
        store = FramedResultTransferStore(FramedResultConfig(frame_payload_bytes=4))
        item = ("result:A", {"p": "data"})
        loser = store.reserve_many([item], owner="x")
        winner = store.reserve_many([item], owner="x")
        loser.commit()
        winner.commit()
        winner.confirm()  # the winner's external step succeeded
        loser.withdraw()
        transfer_id = winner.frames[0][0].transfer_id
        self.assertEqual(("x",), store.retained_owner(transfer_id))
        # And the other way round: the confirmation came first.
        later = store.reserve_many([item], owner="x")
        later.commit()
        later.withdraw()
        self.assertEqual(("x",), store.retained_owner(transfer_id))

    def test_a_copy_an_undecided_delivery_still_needs_is_kept_when_another_fails(self) -> None:
        store = FramedResultTransferStore(FramedResultConfig(frame_payload_bytes=4))
        item = ("result:A", {"p": "data"})
        undecided = store.reserve_many([item], owner="x")
        failing = store.reserve_many([item], owner="x")
        undecided.commit()
        failing.commit()
        failing.withdraw()
        transfer_id = undecided.frames[0][0].transfer_id
        self.assertEqual(("x",), store.retained_owner(transfer_id))
        undecided.confirm()  # it then succeeds: the copy is the winner's
        self.assertEqual(("x",), store.retained_owner(transfer_id))
        self.assertIn(transfer_id, store._confirmed)

    def test_admit_many_keeps_what_it_admits(self) -> None:
        store = FramedResultTransferStore(FramedResultConfig(frame_payload_bytes=4))
        frames = store.admit(logical_result_identity="result:1", result={"p": "abcdefgh"}, owner="a")
        transfer_id = frames[0].transfer_id
        again = store.reserve_many([("result:1", {"p": "abcdefgh"})], owner="a")
        again.commit()
        again.withdraw()  # a failure elsewhere never frees an admitted copy
        self.assertEqual(("a",), store.retained_owner(transfer_id))
        self.assertIn(transfer_id, store._confirmed)


if __name__ == "__main__":
    unittest.main()

if __name__ == "__main__":
    unittest.main()
