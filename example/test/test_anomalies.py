from example.detect_anomalies import detect_sequence_annomalies

# happy path

def test_valid_sequence():

    payloads = [
        {
            "timestamp": 1000,
            "pv_production_w": 0,
            "house_consumption_w": 3000,
        },
        {
            "timestamp": 1030,
            "pv_production_w": 0,
            "house_consumption_w": 3200,
        },
    ]

    result = detect_sequence_annomalies(payloads)

    assert result == []

def test_timestamp_gap_detected():

    payloads = [
        {
            "timestamp": 1000,
            "pv_production_w": 0,
            "house_consumption_w": 3000,
        },
        {
            "timestamp": 1100,
            "pv_production_w": 0,
            "house_consumption_w": 3200,
        },
    ]

    result = detect_sequence_annomalies(payloads)

    assert len(result) == 1
    assert "timestamp gap" in result[0]

def test_house_consumption_drop_detected():

    payloads = [
        {
            "timestamp": 1000,
            "pv_production_w": 0,
            "house_consumption_w": 9000,
        },
        {
            "timestamp": 1030,
            "pv_production_w": 0,
            "house_consumption_w": 2000,
        },
    ]

    result = detect_sequence_annomalies(payloads)

    assert len(result) == 1
    assert "house consumption drop" in result[0]

def test_pv_at_night_detected():
    payloads = [
        {
            "timestamp": 0,
            "pv_production_w": 500,
            "house_consumption_w": 2000,
        }
    ]
    result = detect_sequence_annomalies(payloads)
    assert len(result) == 1
    assert "night" in result[0].lower()