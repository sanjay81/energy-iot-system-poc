import json

from iot_device.buffer import LocalBuffer


def test_buffer_survives_restart_and_preserves_order(tmp_path):
    path = tmp_path / "buffer.json"
    buffer = LocalBuffer(max_size=3, buffer_file=str(path))
    buffer.add({"timestamp": 1})
    buffer.add({"timestamp": 2})

    reloaded = LocalBuffer(max_size=3, buffer_file=str(path))

    assert reloaded.get_all() == [{"timestamp": 1}, {"timestamp": 2}]


def test_buffer_drops_oldest_record_at_capacity(tmp_path):
    buffer = LocalBuffer(
        max_size=2,
        buffer_file=str(tmp_path / "buffer.json")
    )
    buffer.add({"timestamp": 1})
    buffer.add({"timestamp": 2})
    buffer.add({"timestamp": 3})

    assert buffer.get_all() == [{"timestamp": 2}, {"timestamp": 3}]
    assert buffer.dropped_count == 1


def test_buffer_file_is_always_valid_json(tmp_path):
    path = tmp_path / "buffer.json"
    buffer = LocalBuffer(buffer_file=str(path))
    buffer.add({"timestamp": 1, "value": 42})

    assert json.loads(path.read_text()) == [{"timestamp": 1, "value": 42}]
