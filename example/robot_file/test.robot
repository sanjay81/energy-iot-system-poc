*** Settings ***

Library    Collections
Library    PayloadValidator.py

*** Test cases ***

Valid payload Should Pass
    #&{payload}= Create Dictionary
    &{payload}=    Create Dictionary

    ...    device_id=energy_iot_001
    ...    timestamp=${1779008267.28}
    ...    pv_production_w=${3500.0}
    ...    house_consumption_w=${2000.0}
    ...    battery_power_w=${1000.0}
    ...    grid_power_w=${500.0}

    Validate Payload    ${payload}

Invalid payload Should Fail
    ${payload}=    Create Dictionary

    ...    device_id=energy_iot_001
    ...    timestamp=${1779008267.28}
    ...    pv_production_w=${3000.0}
    ...    house_consumption_w=${2000.0}
    ...    battery_power_w=${1000.0}
    ...    grid_power_w=${500.0}

    Run Keyword And Expect Error   Validate Payload    ${payload}
