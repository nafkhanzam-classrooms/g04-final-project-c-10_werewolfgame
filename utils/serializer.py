import json

def encode(data: dict) -> bytes:
    return (json.dumps(data) + "\n").encode("utf-8")

def decode(raw: str) -> dict:
    return json.loads(raw.strip())