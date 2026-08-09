import threading
from types import SimpleNamespace

from iot_device.buffer import LocalBuffer
from iot_device.mqtt_publisher import MQTTPublisher, TOPIC_MEASUREMENTS


class FakeMQTTClient:
    def __init__(self):
        self.messages = []
        self.next_mid = 10

    def publish(self, topic, payload, qos=0, retain=False):
        mid = self.next_mid
        self.next_mid += 1
        self.messages.append((topic, payload, qos))
        return SimpleNamespace(rc=0, mid=mid)


def build_publisher(tmp_path):
    publisher = MQTTPublisher.__new__(MQTTPublisher)
    publisher.client = FakeMQTTClient()
    publisher.buffer = LocalBuffer(buffer_file=str(tmp_path / "buffer.json"))
    publisher._pending_replay_mid = None
    publisher._lock = threading.Lock()
    return publisher


def test_replay_waits_for_ack_before_removing_record(tmp_path):
    publisher = build_publisher(tmp_path)
    publisher.buffer.add({"timestamp": 1})
    publisher.buffer.add({"timestamp": 2})

    publisher._upload_buffer()

    assert publisher.buffer.size == 2
    assert publisher.client.messages[0][0] == TOPIC_MEASUREMENTS

    publisher._on_publish(None, None, 10)

    assert publisher.buffer.size == 1
    assert len(publisher.client.messages) == 2

    publisher._on_publish(None, None, 11)
    assert publisher.buffer.is_empty
