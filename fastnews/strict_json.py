"""Shared JSON boundary: reject duplicate fields and nonfinite numbers."""
import json


def loads(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('JSON contains a duplicate field')
            result[key] = value
        return result
    def invalid(value):
        raise ValueError('JSON contains a nonfinite number')
    return json.loads(text, object_pairs_hook=unique, parse_constant=invalid)
