"""
A class-based Python library
One keyword: Validate Payload
It calls your existing validate_payload function
If violations found → raise AssertionError with the messages
If clean → pass silently
"""

from validator import validate_payload

class PayloadValidator:
    """Robot Framework library for validating IoT energy payloads.
    """
    def validate_payload(self, payload):
        """
        Robot keyword to validate payload
        """
        errors = validate_payload(payload)
        if errors:
                raise AssertionError("Validating payload failed " + ",".join(errors))
