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
    "cancel_action": {},
    "ping":        {"t":        ((int, float), None, None)},
    "players":     {},
}


def validate_packet(packet):
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