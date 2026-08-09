'''
Challenges: making keyword syntax feel natural (underscores become spaces),
handling async message arrival (polling with timeout),
and surfacing useful errors when Robot catches exceptions.

example: an MQTTLibrary wrapping paho-mqtt for testing device communication.
'''

import paho.mqtt.client as mqtt
from robot.api import logger
from robot.api.deco import keyword
import queue

class MQTTLibrary:
    ROBOT_LIBRARY_SCOPE = "TEST SUITE"

    def __init__(self):
        self._message = queue.Queue()
        # internal/private client
        self._client = mqtt.Client()

        # register callback
        self._client.on_message = self.on_message

    def on_message(self, client, userdata, msg):
        self._message.put(msg)

    @keyword("Connect to MQTT broker")
    def connect(self, host, port = 1883):
        self._client.connect(host, int(port))
        self._client.loop_start()
        logger.info(f"Connected to {host}:{port}")


    @keyword("Wait for MQTT message on Topic")
    def wait_for_message(self, topic, timeout= 10):
        self._client.subscribe(topic)
        try:
            msg = self._message.get(timeout = float(timeout))
            return msg.payload.decode()
        except queue.Empty:
            raise AssertionError(f"No Message on {topic} within {timeout}s")

