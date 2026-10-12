"""Sort values as users see them, with numeric sizes and creation dates."""

from datetime import datetime
import re


def natural_key(value):
    return tuple(int(part) if part.isdigit() else part.casefold()
                 for part in re.split(r"(\d+)", value))


def size_bytes(value):
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmgtpe]?i?b)\s*", value, re.IGNORECASE)
    if not match:
        return -1
    amount, unit = match.groups()
    unit = unit.lower()
    power = "bkmgtpe".index(unit[0]) if unit[0] != "b" else 0
    return float(amount) * (1024 if "i" in unit else 1000) ** power


def creation_time(value):
    match = re.match(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})\s*([+-]\d{2}:?\d{2}|Z)", value)
    if not match:
        return float('-inf')
    try:
        return datetime.fromisoformat(match[1] + match[2]).timestamp()
    except ValueError:
        return float('-inf')


def sort_value(item, key, states=None):
    if key == "host":
        return natural_key(item.host_label)
    value = item.data.get(key, "")
    if key == "Size":
        return size_bytes(value)
    if key == "CreatedSince":
        return creation_time(item.data.get("CreatedAt", ""))
    if key == "State":
        value = (states or {}).get(value, value)
    return natural_key(value)
