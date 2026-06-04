import unittest

from Agents.event_bus import EventBus, build_event


class EventBusTests(unittest.TestCase):
    def test_build_event_has_standard_shape(self):
        event = build_event(
            session_id="s1",
            turn_id=1,
            stage="controller",
            event_type="user_input_received",
            payload={"text": "公司辞退我"},
        )

        self.assertEqual(event["session_id"], "s1")
        self.assertEqual(event["event_type"], "user_input_received")
        self.assertIn("event_id", event)
        self.assertIn("created_at_utc", event)

    def test_publish_calls_subscribers_in_order(self):
        bus = EventBus()
        calls = []
        bus.subscribe("user_input_received", lambda event: calls.append(("first", event["payload"]["text"])))
        bus.subscribe("user_input_received", lambda event: calls.append(("second", event["payload"]["text"])))

        bus.publish(
            build_event(
                session_id="s1",
                turn_id=1,
                stage="controller",
                event_type="user_input_received",
                payload={"text": "x"},
            )
        )

        self.assertEqual(calls, [("first", "x"), ("second", "x")])

    def test_patch_events_are_replayable(self):
        bus = EventBus()
        recorded = []
        bus.subscribe("patch_submitted", recorded.append)
        bus.subscribe("patch_applied", recorded.append)

        bus.publish(build_event("s1", 1, "controller", "patch_submitted", {"patch_id": "p1"}))
        bus.publish(build_event("s1", 1, "controller", "patch_applied", {"patch_id": "p1"}))

        self.assertEqual([event["event_type"] for event in recorded], ["patch_submitted", "patch_applied"])


if __name__ == "__main__":
    unittest.main()
