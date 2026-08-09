payloads = ["a", "b", "c"]

# only get the value
for payload in payloads:
    print(payload)

print("\n")

# check value and postions

for i in range(len(payloads)):
    print(i, payloads[i])

print("\n")

 # use enumeration - gives items and it's position
for index, payload in enumerate(payloads):
     print(index, payload)