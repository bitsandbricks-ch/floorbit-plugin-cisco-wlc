"""Catalyst 9800 over RESTCONF. A fake controller, never a real one."""
import pytest
import requests
from floorbit import wifi
from floorbit.plugins.base import PluginError

AP_OPER = "Cisco-IOS-XE-wireless-access-point-oper"
RRM_OPER = "Cisco-IOS-XE-wireless-rrm-oper"
MAC_1, MAC_2 = "02:00:00:00:01:00", "02:00:00:00:02:00"

CAPWAP = [
    {"wtp-mac": MAC_1, "name": "ap-hall-01", "ap-state": {"ap-operation-state": "registered"}, "ip-addr": "192.0.2.101",
     "device-detail": {"static-info": {"ap-models": {"model": "C9130AXI-E"},
                                       "board-data": {"wtp-serial-num": "FOC0000X001"}}}},
    {"wtp-mac": MAC_2, "name": "ap-hall-02", "ap-state": {"ap-operation-state": "ap-down"},
     "device-detail": {"static-info": {"ap-models": {"model": "C9120AXI-E"}}}},
]
RADIOS = [
    {"wtp-mac": MAC_1, "radio-slot-id": 0, "radio-type": "dot11-radio-type-802-11bg", "oper-state": "radio-up",
     "current-active-band": "dot11-2-dot-4-ghz-band", "phy-ht-cfg": {"cfg-data": {"curr-freq": 6, "chan-width": 20}}},
    {"wtp-mac": MAC_1, "radio-slot-id": 1, "radio-type": "dot11-radio-type-802-11a", "oper-state": "radio-up",
     "current-active-band": "dot11-5-ghz-band",
     "phy-ht-cfg": {"cfg-data": {"curr-freq": 52, "chan-width": "chan-width-80-mhz"}}},
    {"wtp-mac": MAC_1, "radio-slot-id": 2, "radio-type": "dot11-radio-type-802-11a", "oper-state": "radio-down",
     "current-active-band": "dot11-6-ghz-band", "phy-ht-cfg": {"cfg-data": {"curr-freq": 37, "chan-width": 160}}},
]
LOADS = [
    {"wtp-mac": MAC_1, "radio-slot-id": 0, "load": {"cca-util-percentage": 61, "stations": 7}},
    {"wtp-mac": MAC_1, "radio-slot-id": 1, "load": {"cca-util-percentage": 23, "stations": 31}},
]


class Answer:
    def __init__(self, body=None, status=200):
        self.status_code = status
        self._body = body

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not JSON")
        return self._body


@pytest.fixture
def controller(monkeypatch):
    calls = []
    state = {"password": "example-password", "aps": CAPWAP}

    def fake(self, method, url, **kw):
        calls.append((method, url, kw.get("headers", {}).get("Accept")))
        if self.auth != ("floorbit", state["password"]):
            return Answer({}, 401)
        path = url.removeprefix("https://wlc.example.com/restconf/data/")
        if path.endswith("/capwap-data"):
            return Answer({f"{AP_OPER}:capwap-data": state["aps"]}) if state["aps"] else Answer(None, 404)
        if path.endswith("/radio-oper-data"):
            return Answer({f"{AP_OPER}:radio-oper-data": RADIOS})
        if path.endswith("/rrm-measurement"):
            return Answer({f"{RRM_OPER}:rrm-measurement": LOADS})
        return Answer({}, 404)

    monkeypatch.setattr(requests.Session, "request", fake)
    return calls, state


def make_9800(make, **extra):
    return make(platform="catalyst-9800", url="https://wlc.example.com", username="floorbit",
                password="example-password", **extra)


def test_access_points_radios_and_load(make, controller):
    calls, _ = controller
    aps = [wifi.clean_ap(a) for a in make_9800(make).wifi_aps()]
    assert [a["name"] for a in aps] == ["ap-hall-01", "ap-hall-02"]
    one, two = aps
    assert one["model"] == "C9130AXI-E" and one["status"] == "up" and one["clients"] == 38
    assert one["radios"] == [{"band": "2.4", "channel": 6, "width": 20, "utilization": 61, "clients": 7},
                             {"band": "5", "channel": 52, "width": 80, "utilization": 23, "clients": 31}]
    assert two["status"] == "down" and two["radios"] == [] and two["clients"] == 0
    assert {accept for _m, _u, accept in calls} == {"application/yang-data+json"}
    assert len(calls) == 3


def test_no_access_points_yet_is_an_empty_list(make, controller):
    controller[1]["aps"] = []
    assert make_9800(make).wifi_aps() == []


def test_a_wrong_password_says_so(make, controller):
    with pytest.raises(PluginError, match="refused the username or password"):
        make(platform="catalyst-9800", url="https://wlc.example.com", username="floorbit",
             password="wrong").wifi_aps()


def test_test_button(make, controller):
    assert make_9800(make).test() == "Connected: 2 access points, 1 up."


def test_unset_says_what_is_missing(make):
    with pytest.raises(PluginError, match="controller URL, username and password"):
        make(platform="catalyst-9800").wifi_aps()


def test_access_points_into_the_inventory(make, controller):
    from floorbit import inventory
    items, skipped = inventory.clean_feed(make(url="https://wlc.example.com", username="floorbit",
                                               password="example-password").inventory())
    assert skipped == 0
    by = {i["ref"]: i for i in items}
    assert by[f"ap/{MAC_1}"] == {"ref": f"ap/{MAC_1}", "name": "ap-hall-01", "role": "access-point",
                                 "model": "C9130AXI-E", "serial": "FOC0000X001", "mgmt_ip": "192.0.2.101",
                                 "platform": "cisco-ap",
                                 "status": "active"}
    assert by[f"ap/{MAC_2}"]["status"] == "offline" and "serial" not in by[f"ap/{MAC_2}"]
