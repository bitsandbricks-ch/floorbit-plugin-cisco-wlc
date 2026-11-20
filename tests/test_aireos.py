"""AireOS over SNMP v2c. A fake SNMP agent on a local UDP port, never a real controller."""
import socket
import sys
import threading

import pytest
from conftest import load
from floorbit import wifi
from floorbit.plugins.base import PluginError

snmp = load("snmp")

AP, IF, LOAD = "1.3.6.1.4.1.14179.2.2.1.1", "1.3.6.1.4.1.14179.2.2.2.1", "1.3.6.1.4.1.14179.2.2.13.1"
MAC_1, MAC_2 = "2.0.0.0.1.0", "2.0.0.0.2.0"

# (tag, raw) per OID, as the controller would hold them
MIB = {
    f"{AP}.3.{MAC_1}": (snmp.OCTETS, b"ap-hall-01"), f"{AP}.3.{MAC_2}": (snmp.OCTETS, b"ap-hall-02"),
    f"{AP}.6.{MAC_1}": (snmp.INTEGER, 1), f"{AP}.6.{MAC_2}": (snmp.INTEGER, 2),
    f"{AP}.16.{MAC_1}": (snmp.OCTETS, b"AIR-AP2802I-E-K9"), f"{AP}.16.{MAC_2}": (snmp.OCTETS, b"AIR-AP1852I-E-K9"),
    f"{AP}.17.{MAC_1}": (snmp.OCTETS, b"FCW0000X002"), f"{AP}.19.{MAC_1}": (snmp.IP_ADDRESS, bytes([192, 0, 2, 102])),
    f"{IF}.2.{MAC_1}.0": (snmp.INTEGER, 1), f"{IF}.2.{MAC_1}.1": (snmp.INTEGER, 2),
    f"{IF}.4.{MAC_1}.0": (snmp.INTEGER, 11), f"{IF}.4.{MAC_1}.1": (snmp.INTEGER, 149),
    f"{IF}.12.{MAC_1}.0": (snmp.INTEGER, 2), f"{IF}.12.{MAC_1}.1": (snmp.INTEGER, 2),
    f"{IF}.15.{MAC_1}.0": (snmp.INTEGER, 4), f"{IF}.15.{MAC_1}.1": (snmp.INTEGER, 19),
    f"{IF}.4.{MAC_2}.0": (snmp.INTEGER, 1), f"{IF}.12.{MAC_2}.0": (snmp.INTEGER, 1),
    f"{LOAD}.3.{MAC_1}.0": (0x42, 47), f"{LOAD}.3.{MAC_1}.1": (0x42, 12),
    f"{LOAD}.4.{MAC_1}.0": (0x42, 5), f"{LOAD}.4.{MAC_1}.1": (0x42, 20),
    "1.3.6.1.4.1.14179.2.3.1.1": (snmp.INTEGER, 0),           # past the tables, so walks stop
}


def oid_key(oid):
    return tuple(int(p) for p in oid.split("."))


def encode_value(tag, value):
    if tag in (snmp.OCTETS, snmp.IP_ADDRESS):
        return snmp.tlv(tag, value)
    if tag == snmp.INTEGER:
        return snmp.enc_int(value)
    return snmp.tlv(tag, value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big"))


def answer(request, community):
    """The fake agent's RESPONSE to one GETBULK, or None for a wrong community."""
    _tag, message, _ = snmp.read(request)
    parts = list(snmp.items(message))
    if parts[1][1].decode() != community:
        return None
    pdu = list(snmp.items(parts[2][1]))
    request_id, reps = snmp.value_of(*pdu[0]), snmp.value_of(*pdu[2])
    (_t, vb), = list(snmp.items(pdu[3][1]))
    start = snmp.dec_oid(next(snmp.items(vb))[1])
    after = sorted((o for o in MIB if oid_key(o) > oid_key(start)), key=oid_key)[:reps]
    binds = b"".join(snmp.tlv(snmp.SEQUENCE, snmp.enc_oid(o) + encode_value(*MIB[o])) for o in after)
    if len(after) < reps:
        binds += snmp.tlv(snmp.SEQUENCE, snmp.enc_oid(after[-1] if after else start) + snmp.tlv(0x82, b""))
    pdu = snmp.tlv(snmp.RESPONSE, snmp.enc_int(request_id) + snmp.enc_int(0) + snmp.enc_int(0)
                   + snmp.tlv(snmp.SEQUENCE, binds))
    return snmp.tlv(snmp.SEQUENCE, snmp.enc_int(1) + snmp.tlv(snmp.OCTETS, community.encode()) + pdu)


@pytest.fixture
def agent():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(0.2)
    stop = threading.Event()
    seen = []

    def serve():
        while not stop.is_set():
            try:
                data, peer = sock.recvfrom(65535)
            except TimeoutError:
                continue
            seen.append(data)
            reply = answer(data, "example-community")
            if reply:
                sock.sendto(reply, peer)

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    yield sock.getsockname()[1], seen
    stop.set()
    t.join(2)
    sock.close()


def test_oid_and_integer_round_trip():
    for oid in ("1.3.6.1.4.1.14179.2.2.1.1", "1.3.6.1.2.1.1.1.0", "1.3.6.1.4.1.9.9.513.1.1.1.1.5.128.200"):
        assert snmp.dec_oid(snmp.enc_oid(oid)[2:]) == oid
    for n in (0, 1, 127, 128, 255, 256, 65535, -1, 2**31 - 1):
        tag, raw, _ = snmp.read(snmp.enc_int(n))
        assert snmp.value_of(tag, raw) == n


def test_walk_pages_through_a_table(agent):
    port, seen = agent
    got = snmp.walk("127.0.0.1", "example-community", IF, port=port, repetitions=3)
    assert got[f"4.{MAC_1}.1"] == 149 and len(got) == 10
    assert len(seen) >= 4          # 10 rows, 3 at a time


def point_at(plugin, monkeypatch, port, **kw):
    """The plugin's own snmp module, walking the fake agent's port instead of 161."""
    mod = sys.modules[type(plugin).__module__].snmp
    walk = mod.walk
    monkeypatch.setattr(mod, "walk", lambda host, community, base: walk(host, community, base, port=port, **kw))
    return plugin


def test_access_points_radios_and_load(make, agent, monkeypatch):
    port, _ = agent
    plugin = point_at(make(platform="aireos", host="127.0.0.1", community="example-community"), monkeypatch, port)
    aps = [wifi.clean_ap(a) for a in plugin.wifi_aps()]
    assert [a["name"] for a in aps] == ["ap-hall-01", "ap-hall-02"]
    one, two = aps
    assert one["model"] == "AIR-AP2802I-E-K9" and one["status"] == "up" and one["clients"] == 25
    assert one["radios"] == [{"band": "2.4", "channel": 11, "width": None, "utilization": 47, "clients": 5},
                             {"band": "5", "channel": 149, "width": None, "utilization": 12, "clients": 20}]
    assert two["status"] == "down" and two["radios"] == []


def test_a_wrong_community_says_so(make, agent, monkeypatch):
    port, _ = agent
    plugin = point_at(make(platform="aireos", host="127.0.0.1", community="wrong"), monkeypatch, port,
                      timeout=0.3, retries=0)
    with pytest.raises(PluginError, match="No SNMP answer"):
        plugin.wifi_aps()


def test_unset_says_what_is_missing(make):
    with pytest.raises(PluginError, match="SNMP address and community"):
        make(platform="aireos").wifi_aps()


def test_access_points_into_the_inventory(make, agent, monkeypatch):
    from floorbit import inventory
    port, _ = agent
    plugin = point_at(make(platform="aireos", host="127.0.0.1", community="example-community"), monkeypatch, port)
    items, skipped = inventory.clean_feed(plugin.inventory())
    by = {i["ref"]: i for i in items}
    assert skipped == 0 and set(by) == {"ap/02:00:00:00:01:00", "ap/02:00:00:00:02:00"}
    one = by["ap/02:00:00:00:01:00"]
    assert (one["name"], one["role"], one["serial"], one["mgmt_ip"], one["status"]) == \
        ("ap-hall-01", "access-point", "FCW0000X002", "192.0.2.102", "active")
    assert by["ap/02:00:00:00:02:00"]["status"] == "offline"
