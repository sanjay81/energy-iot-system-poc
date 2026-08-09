*** Settings ***
Library    MQTTLibrary.py

*** Test Cases ***
Test MQTT Message
    Connect To MQTT Broker    localhost    1883
    ${msg}=    Wait For MQTT Message On Topic    energy-iot/measurements    30
    Should Not Be Empty    ${msg}
