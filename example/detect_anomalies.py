from datetime import datetime

print(datetime.fromtimestamp(1748000000).hour)

def is_that_night(timestamp: float) -> bool:
    hour = datetime.fromtimestamp(timestamp).hour
    return hour >= 22 or hour < 5

def detect_sequence_annomalies(payloads: list[dict]) -> list[str]:
    anomalies = []

    # read vlaue safely
    for index, payload in enumerate(payloads):
        timestamp = payload.get("timestamp")
        pv_production = payload.get("pv_production_w")
        house_consumption = payload.get("house_consumption_w")

        # Missing timestamp check
        if timestamp is None:
            anomalies.append(f"payload {index}: missing timestamp")
            continue

        # Night PV check
        if pv_production is None:
            anomalies.append(f"payload {index}: missing pv_production")

        if house_consumption is None:
            anomalies.append(f"payload {index}: missing house_consuption")

        #check when pv =0 in night
        if pv_production is not None and is_that_night(timestamp) and pv_production > 0:
            anomalies.append(f"payload {index}: pv production {pv_production}w detect at night")

        if index == 0:
            continue

        # get pervoius payload
        pervious = payloads[index -1]
        pervious_timestamp = pervious.get("timestamp")
        pervious_house = pervious.get("house_consumption_w")

        # timestamp gap
        if pervious_timestamp is not None:
            gap = timestamp - pervious_timestamp
            if gap > 60:
                anomalies.append(
                        f"payload {index} timestamp gap {gap:.1f}s exceeds 60s"
                )
        if pervious_house is not None and house_consumption is not None:
            drop = pervious_house - house_consumption
            if drop > 5000:
                anomalies.append(
                    f"payload {index} house consumption drop {drop:.1f}w "
                )

    return anomalies

if __name__=="__main__":

    print("Program stated")
    payloads = [
        {
            "timestamp": 1000,
            "pv_production_w": 0,
            "house_consumption_w":9000,
        },
        {
            "timestamp": 1090,   # 90s gap
            "pv_production_w": 0,
            "house_consumption_w": 2000
        }
    ]

    results = detect_sequence_annomalies(payloads)
    print("Result:", results)


    for annomaly in results:
        print(annomaly)
    print("program stop")
