"""
Packet validation for the Werewolf game protocol.

Every inbound packet on the server is checked against PACKET_SCHEMA before
dispatch. Invalid packets are rejected with an `error` response and never
reach the per-type handler. This is the first line of defense against:

  - Malformed packets (missing required fields, wrong field types)
  - Oversized strings (chat spam, pathological usernames)
  - Unknown packet types
  - Non-object payloads (lists, numbers, strings, null)

It does NOT enforce: game-state validity (you can't `vote` outside VOTING
phase), authorization (only wolves can `kill`), or rate limiting. Those
checks live in the per-handler logic in packet_handler.py.

Schema format:
    PACKET_SCHEMA[ptype] = {field_name: (expected_type, min_len, max_len)}

  - For strings, min_len/max_len bound the string length.
    (min_len is checked after .strip(), so empty/whitespace-only is rejected.)
  - For other types, min_len/max_len are ignored — pass (type, None, None).
  - expected_type can be a tuple of types, e.g. (int, float) for numbers.
  - Packets with no required fields use an empty dict.
  - Extra fields beyond the schema are allowed (forward compatibility).
"""

PACKET_SCHEMA = {
    "register":    {"username": (str, 1, 32),  "password": (str, 1, 128)},
    "login":       {"username": (str, 1, 32),  "password": (str, 1, 128)},
    "create":      {"room":     (str, 1, 16)},
    "join":        {"room":     (str, 1, 16)},
    "leave":       {},
    "rooms":       {},
    "ready":       {"status":   (bool, None, None)},
    "start":       {},
    "chat":        {"msg":      (str, 1, 500)},
    "kill":        {"target":   (str, 1, 32)},
    "protect":     {"target":   (str, 1, 32)},
    "check":       {"target":   (str, 1, 32)},
    "vote":        {"target":   (str, 1, 32)},
    "hunter_shot": {"target":   (str, 1, 32)},
    "ping":        {"t":        ((int, float), None, None)},
    "players":     {},
}


def validate_packet(packet):
    """
    Returns (ok, error_msg).

      - ok=True   -> packet is structurally valid, safe to dispatch.
      - ok=False  -> error_msg is a short human-readable explanation.

    This function never raises. Any input (None, lists, numbers, etc.) is
    handled gracefully.
    """
    if not isinstance(packet, dict):
        return False, "Packet must be a JSON object"

    ptype = packet.get("type")
    if not isinstance(ptype, str) or not ptype:
        return False, "Missing or invalid 'type' field"

    schema = PACKET_SCHEMA.get(ptype)
    if schema is None:
        return False, f"Unknown packet type: {ptype}"

    for field, (expected_type, min_len, max_len) in schema.items():
        if field not in packet:
            return False, f"Missing required field: '{field}'"

        value = packet[field]

        # bool is a subclass of int in Python — explicitly reject bool when
        # we expect numbers (e.g. ping.t).
        if isinstance(value, bool) and expected_type != bool and not (
            isinstance(expected_type, tuple) and bool in expected_type
        ):
            return False, f"Field '{field}' has wrong type"

        if not isinstance(value, expected_type):
            return False, f"Field '{field}' has wrong type"

        if isinstance(value, str):
            stripped = value.strip()
            if min_len is not None and len(stripped) < min_len:
                return False, f"Field '{field}' is empty or too short"
            if max_len is not None and len(value) > max_len:
                return False, f"Field '{field}' is too long (max {max_len})"

    return True, ""