#!/usr/bin/env python3
"""Tests for core.events: the bus, its cursors, its required bounds and its exact resume.

What matters: a stream with no bound refuses to exist, a resume says HOW MANY events were lost (never
silently skipping), a restart (a new epoch) is a reset and not loss, a failing subscriber is recorded and
never breaks publishing or other subscribers, and under real concurrency a resuming reader sees every
event exactly once.
Run:  python3 tests/core/test_events.py
"""
import json
import math
import os
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from nrf_workbench.core.events import BoundError, Bus  # noqa: E402


class Clock(object):
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def bus(retain=100, max_age_s=1000, clock=None):
    clock = clock or Clock()
    return Bus(retain, max_age_s, mono=clock, wall=lambda: 1700000000.0 + clock.t), clock


class Bounds(unittest.TestCase):
    def test_a_stream_with_no_usable_retain_refuses_to_exist(self):
        for bad in (None, 0, -1, True, 1.5, "10", [], float("inf")):
            with self.assertRaises(BoundError, msg=repr(bad)) as caught:
                Bus(bad, 10)
            self.assertEqual(caught.exception.code, "no_bound")
        self.assertTrue(issubclass(BoundError, ValueError))

    def test_a_stream_with_no_usable_age_refuses_to_exist(self):
        for bad in (None, 0, -1, True, "1", math.inf, math.nan, []):
            with self.assertRaises(BoundError, msg=repr(bad)):
                Bus(10, bad)

    def test_there_is_no_default_for_either_bound(self):
        with self.assertRaises(TypeError):
            Bus()
        with self.assertRaises(TypeError):
            Bus(10)

    def test_the_smallest_legal_bounds_work(self):
        b = Bus(1, 0.001)
        self.assertEqual((b.retain, b.max_age_s), (1, 0.001))


class Publishing(unittest.TestCase):
    def test_cursors_start_at_1_and_only_go_up(self):
        b, _ = bus()
        self.assertEqual([b.publish("t", "a").cursor for _ in range(4)], [1, 2, 3, 4])
        self.assertEqual(b.latest, 4)

    def test_an_event_carries_its_type_area_payload_and_both_timestamps(self):
        b, clock = bus()
        clock.t = 12.5
        e = b.publish("devices.changed", "devices", {"serial": "S"})
        self.assertEqual((e.type, e.area, e.payload, e.mono, e.wall), ("devices.changed", "devices", {"serial": "S"},
                                                                      12.5, 1700000012.5))
        self.assertEqual(sorted(e.to_dict()), ["area", "cursor", "mono", "payload", "type", "wall"])
        json.dumps(e.to_dict())

    def test_the_payload_is_copied_so_the_caller_cannot_change_a_published_event(self):
        b, _ = bus()
        payload = {"n": 1}
        e = b.publish("t", "a", payload)
        payload["n"] = 2
        self.assertEqual(e.payload, {"n": 1})

    def test_no_payload_is_an_empty_one(self):
        b, _ = bus()
        self.assertEqual(b.publish("t", "a").payload, {})

    def test_bad_arguments_are_refused_and_publish_nothing(self):
        b, _ = bus()
        for args in (("", "a"), (None, "a"), (5, "a"), ("t", ""), ("t", None)):
            with self.assertRaises(ValueError, msg=args):
                b.publish(*args)
        for payload in ([1], "x", 5):
            with self.assertRaises(TypeError):
                b.publish("t", "a", payload)
        self.assertEqual(b.latest, 0)


class Reading(unittest.TestCase):
    def test_since_zero_is_everything_and_since_n_is_what_came_after(self):
        b, _ = bus()
        for i in range(5):
            b.publish("t", "a", {"i": i})
        page = b.since(0)
        self.assertEqual(([e.cursor for e in page.events], page.lost, page.reset, page.latest), ([1, 2, 3, 4, 5], 0, False, 5))
        self.assertEqual([e.cursor for e in b.since(3).events], [4, 5])
        self.assertEqual(b.since(5).events, [])

    def test_resuming_at_the_latest_is_empty_not_lost_and_not_a_reset(self):
        b, _ = bus()
        b.publish("t", "a")
        page = b.since(b.latest)
        self.assertEqual((page.events, page.lost, page.reset), ([], 0, False))

    def test_an_empty_bus(self):
        b, _ = bus()
        page = b.since(0)
        self.assertEqual((page.events, page.lost, page.reset, page.latest), ([], 0, False, 0))

    def test_types_filter(self):
        b, _ = bus()
        b.publish("x", "a")
        b.publish("y", "a")
        b.publish("x", "a")
        self.assertEqual([e.cursor for e in b.since(0, types=("x",)).events], [1, 3])
        self.assertEqual([e.cursor for e in b.since(0, types=("z",)).events], [])

    def test_bad_cursors_are_refused(self):
        b, _ = bus()
        for bad in (-1, True, "3", 1.5, None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                b.since(bad)

    def test_the_page_is_json(self):
        b, _ = bus()
        b.publish("t", "a", {"k": "v"})
        d = b.since(0).to_dict()
        json.dumps(d)
        self.assertEqual(sorted(d), ["epoch", "events", "latest", "lost", "reset"])


class Retention(unittest.TestCase):
    def test_the_count_bound_drops_the_oldest_and_says_how_many_were_lost(self):
        b, _ = bus(retain=3)
        for _ in range(5):
            b.publish("t", "a")
        self.assertEqual([e.cursor for e in b.since(0).events], [3, 4, 5])
        self.assertEqual(b.since(0).lost, 2)
        self.assertEqual(b.since(1).lost, 1)
        self.assertEqual(b.since(2).lost, 0, "having seen 2, the next retained is 3: nothing between was lost")
        self.assertEqual([e.cursor for e in b.since(3).events], [4, 5])

    def test_cursors_are_never_reused_after_pruning(self):
        b, _ = bus(retain=2)
        for _ in range(10):
            b.publish("t", "a")
        self.assertEqual([e.cursor for e in b.since(0).events], [9, 10])
        self.assertEqual(b.publish("t", "a").cursor, 11)

    def test_the_age_bound_drops_events_older_than_it_at_publish_and_at_read(self):
        b, clock = bus(retain=100, max_age_s=100)
        b.publish("t", "a")                       # t=0
        clock.t = 50
        b.publish("t", "a")                       # t=50
        clock.t = 101
        b.publish("t", "a")                       # t=101: the first is 101 old, over the bound
        self.assertEqual([e.cursor for e in b.since(0).events], [2, 3])
        clock.t = 151.5                           # nothing published, but the second is now over the bound
        page = b.since(0)
        self.assertEqual([e.cursor for e in page.events], [3])
        self.assertEqual(page.lost, 2, "events pruned by age are counted as lost too")

    def test_the_bound_holds_with_nobody_reading(self):
        # since() prunes too, so a stream nobody reads would grow without limit if publish did not prune.
        b, clock = bus(retain=3, max_age_s=100)
        for _ in range(50):
            b.publish("t", "a")
        self.assertEqual(len(b), 3)
        clock.t = 500
        b.publish("t", "a")
        self.assertEqual(len(b), 1, "by age as well, at publish, with no read in between")

    def test_exactly_the_maximum_age_is_still_retained(self):
        b, clock = bus(retain=100, max_age_s=100)
        b.publish("t", "a")
        clock.t = 100
        self.assertEqual(len(b.since(0).events), 1)
        clock.t = 100.001
        self.assertEqual(len(b.since(0).events), 0)

    def test_everything_pruned_is_all_lost_and_not_a_reset(self):
        b, clock = bus(retain=100, max_age_s=10)
        for _ in range(3):
            b.publish("t", "a")
        clock.t = 1000
        page = b.since(0)
        self.assertEqual((page.events, page.lost, page.reset), ([], 3, False))
        self.assertEqual(b.since(3).lost, 0)


class Epochs(unittest.TestCase):
    def test_every_bus_has_its_own_epoch(self):
        self.assertNotEqual(bus()[0].epoch, bus()[0].epoch)

    def test_a_client_holding_another_epoch_is_told_to_reset_not_that_events_were_lost(self):
        old, _ = bus()
        for _ in range(7):
            old.publish("t", "a")
        restarted, _ = bus()                      # the service restarted: cursors start again
        restarted.publish("t", "a")
        page = restarted.since(7, epoch=old.epoch)
        self.assertTrue(page.reset)
        self.assertIsNone(page.lost, "after a restart the loss is unknowable, so it is not claimed")
        self.assertEqual([e.cursor for e in page.events], [1])
        self.assertEqual(page.epoch, restarted.epoch)

    def test_a_reset_page_honours_the_type_filter_too(self):
        old, _ = bus()
        restarted, _ = bus()
        restarted.publish("x", "a")
        restarted.publish("y", "a")
        restarted.publish("x", "a")
        page = restarted.since(0, epoch=old.epoch, types=("x",))
        self.assertEqual(([e.type for e in page.events], page.reset), (["x", "x"], True))

    def test_the_same_epoch_is_a_normal_resume(self):
        b, _ = bus()
        for _ in range(3):
            b.publish("t", "a")
        page = b.since(1, epoch=b.epoch)
        self.assertEqual((page.reset, page.lost, [e.cursor for e in page.events]), (False, 0, [2, 3]))

    def test_a_cursor_from_the_future_is_a_reset(self):
        b, _ = bus()
        b.publish("t", "a")
        page = b.since(99)
        self.assertEqual((page.reset, page.lost), (True, None))


class Subscribers(unittest.TestCase):
    def test_every_subscriber_is_called_with_each_event_in_order(self):
        b, _ = bus()
        one, two = [], []
        b.subscribe(lambda e: one.append(e.cursor))
        b.subscribe(lambda e: two.append(e.cursor))
        for _ in range(3):
            b.publish("t", "a")
        self.assertEqual((one, two), ([1, 2, 3], [1, 2, 3]))

    def test_unsubscribe_stops_delivery_and_is_true_once(self):
        b, _ = bus()
        got = []
        token = b.subscribe(lambda e: got.append(e.cursor))
        b.publish("t", "a")
        self.assertTrue(b.unsubscribe(token))
        self.assertFalse(b.unsubscribe(token))
        b.publish("t", "a")
        self.assertEqual(got, [1])

    def test_a_subscriber_may_read_the_bus_from_its_callback_without_deadlock(self):
        b, _ = bus()
        seen = []
        b.subscribe(lambda e: seen.append([x.cursor for x in b.since(0).events]))
        b.publish("t", "a")
        b.publish("t", "a")
        self.assertEqual(seen, [[1], [1, 2]])

    def test_a_failing_subscriber_is_recorded_and_breaks_neither_publishing_nor_the_others(self):
        b, _ = bus()
        got = []

        def broken(event):
            raise RuntimeError("subscriber bug")

        b.subscribe(broken)
        b.subscribe(lambda e: got.append(e.cursor))
        event = b.publish("t", "a")
        self.assertEqual((event.cursor, got), (1, [1]))
        (fault,) = list(b.faults)
        self.assertEqual((fault["subscriber"], fault["cursor"]), ("broken", 1))
        self.assertIn("subscriber bug", fault["error"])

    def test_the_fault_record_is_itself_bounded(self):
        b, _ = bus(retain=3)

        def broken(event):
            raise RuntimeError("x")

        b.subscribe(broken)
        for _ in range(10):
            b.publish("t", "a")
        self.assertEqual(len(b.faults), 3)

    def test_a_subscriber_must_be_callable(self):
        with self.assertRaises(TypeError):
            bus()[0].subscribe("not callable")


class Counting(unittest.TestCase):
    def test_the_subscriber_count_follows_subscribe_and_unsubscribe(self):
        b, _ = bus()
        self.assertEqual(b.subscriber_count, 0)
        t1, t2 = b.subscribe(lambda e: None), b.subscribe(lambda e: None)
        self.assertEqual(b.subscriber_count, 2)
        b.unsubscribe(t1)
        b.unsubscribe(t1)
        self.assertEqual(b.subscriber_count, 1)
        b.unsubscribe(t2)
        self.assertEqual(b.subscriber_count, 0)

    def test_len_is_what_is_held(self):
        b, _ = bus(retain=5)
        self.assertEqual(len(b), 0)
        for _ in range(3):
            b.publish("t", "a")
        self.assertEqual(len(b), 3)


class StreamsHolder(unittest.TestCase):
    def test_it_says_what_is_on_and_why_the_rest_is_off(self):
        from nrf_workbench.core.events import Streams
        s = Streams(events=Bus(1, 1), activity=None, absent={"activity": "no bound configured"})
        self.assertEqual(s.to_dict(), {"events": True, "activity": False, "console": False,
                                       "absent": {"activity": "no bound configured"}})
        self.assertEqual(Streams().to_dict(), {"events": False, "activity": False, "console": False, "absent": {}})
        self.assertTrue(Streams(console=Bus(1, 1)).to_dict()["console"])

    def test_the_absent_map_is_copied(self):
        from nrf_workbench.core.events import Streams
        reasons = {"events": "x"}
        s = Streams(absent=reasons)
        reasons["events"] = "changed"
        self.assertEqual(s.absent, {"events": "x"})


class Concurrency(unittest.TestCase):
    def test_a_resuming_reader_sees_every_event_exactly_once_while_many_threads_publish(self):
        threads_n, per = 8, 250
        b = Bus(threads_n * per, 3600)
        stop = threading.Event()
        collected = []

        def reader():
            cursor = 0
            while True:
                done = stop.is_set()               # read the flag FIRST: one more pass after it is set
                page = b.since(cursor)
                self.assertEqual((page.lost, page.reset), (0, False))
                for e in page.events:
                    collected.append(e.cursor)
                    cursor = e.cursor
                if done:
                    return

        def writer(n):
            for i in range(per):
                b.publish("t", "a", {"w": n, "i": i})

        r = threading.Thread(target=reader)
        r.start()
        writers = [threading.Thread(target=writer, args=(n,)) for n in range(threads_n)]
        for w in writers:
            w.start()
        for w in writers:
            w.join()
        stop.set()
        r.join()
        self.assertEqual(collected, list(range(1, threads_n * per + 1)), "no event lost, none repeated, in order")
        self.assertEqual([e.cursor for e in b.since(0).events], collected)

    def test_per_writer_order_is_kept_in_the_log(self):
        b = Bus(2000, 3600)

        def writer(n):
            for i in range(200):
                b.publish("t", "a", {"w": n, "i": i})

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        by_writer = {}
        for e in b.since(0).events:
            by_writer.setdefault(e.payload["w"], []).append(e.payload["i"])
        for n, order in by_writer.items():
            self.assertEqual(order, list(range(200)), n)


if __name__ == "__main__":
    unittest.main(verbosity=1)
