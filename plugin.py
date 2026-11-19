"""Access points from a Cisco wireless controller, for Floorbit's Wi-Fi view.

Catalyst 9800 (IOS-XE): RESTCONF, three reads of the operational models
(Cisco-IOS-XE-wireless-access-point-oper and -rrm-oper, IOS-XE 17):
``capwap-data`` for each joined AP's name, model and state, ``radio-oper-data`` for each radio's
band, channel and width, and ``rrm-measurement`` for its channel use and clients.

AireOS (3504, 5520, 8540, Mobility Express): SNMP v2c walks of AIRESPACE-WIRELESS-MIB:
bsnAPTable (name, model, state), bsnAPIfTable (channel, radio state, clients) and
bsnAPIfLoadParametersTable (channel use, clients).

Floorbit runs this on the local agent in the hall. Only counts leave: never client names or MACs.
"""
from floorbit.plugins.base import Plugin as Base
from floorbit.plugins.base import PluginError


def _sibling(name):
    # By file, not by package: the plugin runs from wherever it was installed (the Marketplace, or
    # an agent's bundle folder), and neither is an importable package.
    import hashlib
    import importlib.util
    import sys
    from pathlib import Path
    path = Path(__file__).resolve().with_name(f'{name}.py')
    # One module per folder, so a new version's copy never reuses the old one's.
    modname = f'floorbit_plugin_cisco_wlc_{name}_{hashlib.sha256(str(path).encode()).hexdigest()[:12]}'
    if modname not in sys.modules:
        spec = importlib.util.spec_from_file_location(modname, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[modname] = module
        spec.loader.exec_module(module)
    return sys.modules[modname]


snmp = _sibling('snmp')

RESTCONF = '/restconf/data'
AP_OPER = 'Cisco-IOS-XE-wireless-access-point-oper:access-point-oper-data'
RRM_OPER = 'Cisco-IOS-XE-wireless-rrm-oper:rrm-oper-data'
BANDS_9800 = {'dot11-2-dot-4-ghz-band': '2.4', 'dot11-5-ghz-band': '5', 'dot11-6-ghz-band': '6'}

# AIRESPACE-WIRELESS-MIB (enterprise 14179)
BSN_AP = '1.3.6.1.4.1.14179.2.2.1.1'             # bsnAPEntry, index: AP base radio MAC (6 numbers)
BSN_AP_NAME, BSN_AP_STATUS, BSN_AP_MODEL = '3', '6', '16'
BSN_AP_IF = '1.3.6.1.4.1.14179.2.2.2.1'          # bsnAPIfEntry, index: MAC + slot
BSN_IF_TYPE, BSN_IF_CHANNEL, BSN_IF_OPER, BSN_IF_USERS = '2', '4', '12', '15'
BSN_AP_LOAD = '1.3.6.1.4.1.14179.2.2.13.1'       # bsnAPIfLoadParametersEntry, index: MAC + slot
BSN_LOAD_CHANNEL_UTIL, BSN_LOAD_CLIENTS = '3', '4'
BSN_ASSOCIATED, BSN_IF_UP = 1, 2
BSN_IF_BANDS = {1: '2.4', 2: '5'}                # bsnAPIfType: dot11b, dot11a


def _band_from_channel(channel):
    return '2.4' if isinstance(channel, int) and 1 <= channel <= 14 else '5' if channel else None


class Plugin(Base):
    def _platform(self):
        return 'aireos' if self.settings.get('platform') == 'aireos' else 'catalyst-9800'

    def _ready(self):
        if self._platform() == 'aireos':
            return bool(self.settings.get('host') and self.settings.get('community'))
        return bool(self.settings.get('url') and self.settings.get('username') and self.settings.get('password'))

    def wifi_aps(self):
        if not self._ready():
            if self._platform() == 'aireos':
                raise PluginError('Set the SNMP address and community under Settings > Plugins first.')
            raise PluginError('Set the controller URL, username and password under Settings > Plugins first.')
        return self._aireos() if self._platform() == 'aireos' else self._catalyst()

    def test(self):
        aps = self.wifi_aps()
        up = sum(a['status'] == 'up' for a in aps)
        return f'Connected: {len(aps)} access points, {up} up.'

    # Catalyst 9800 -------------------------------------------------------------------------
    def _restconf(self, http, path, key):
        resp = http.get(f'{RESTCONF}/{path}', headers={'Accept': 'application/yang-data+json'})
        if resp.status_code in (401, 403):
            raise PluginError('The controller refused the username or password.')
        if resp.status_code == 404:
            return []            # an empty list is a 404 in RESTCONF: no APs joined yet
        if resp.status_code >= 400:
            raise PluginError(f'The controller answered {resp.status_code}. Is RESTCONF on?')
        try:
            body = resp.json()
        except ValueError as exc:
            raise PluginError("The controller's answer isn't JSON. Is the URL the controller?") from exc
        rows = body.get(key) if isinstance(body, dict) else None
        return rows if isinstance(rows, list) else []

    def _catalyst(self):
        http = self.ctx.http(self.settings.get('url'), timeout=15,
                             auth=(self.settings.get('username'), self.settings.get('password')))
        aps = self._restconf(http, f'{AP_OPER}/capwap-data', 'Cisco-IOS-XE-wireless-access-point-oper:capwap-data')
        radios = self._restconf(http, f'{AP_OPER}/radio-oper-data',
                                'Cisco-IOS-XE-wireless-access-point-oper:radio-oper-data')
        loads = self._restconf(http, f'{RRM_OPER}/rrm-measurement', 'Cisco-IOS-XE-wireless-rrm-oper:rrm-measurement')
        load = {(r.get('wtp-mac'), r.get('radio-slot-id')): r.get('load') or {} for r in loads}
        by_ap = {}
        for r in radios:
            if r.get('radio-type') == 'radio-remote-lan':
                continue
            cfg = (r.get('phy-ht-cfg') or {}).get('cfg-data') or {}
            channel = cfg.get('curr-freq')
            got = load.get((r.get('wtp-mac'), r.get('radio-slot-id')), {})
            by_ap.setdefault(r.get('wtp-mac'), []).append({
                'band': BANDS_9800.get(r.get('current-active-band')) or _band_from_channel(channel),
                'channel': channel, 'width': cfg.get('chan-width') or None,
                'utilization': got.get('cca-util-percentage'),
                'clients': got.get('stations') or 0,
                'up': r.get('oper-state') == 'radio-up'})
        out = []
        for ap in aps:
            name = ap.get('name')
            if not name:
                continue
            static = ((ap.get('device-detail') or {}).get('static-info') or {})
            state = (ap.get('ap-state') or {}).get('ap-operation-state')
            radios = [r for r in by_ap.get(ap.get('wtp-mac'), []) if r.pop('up')]
            out.append({'name': name, 'model': (static.get('ap-models') or {}).get('model') or '',
                        'status': 'up' if state in (None, 'registered', 'ap-up') else 'down',
                        'clients': sum(r['clients'] for r in radios), 'radios': radios})
        return out

    # AireOS --------------------------------------------------------------------------------
    def _aireos(self):
        host, community = str(self.settings.get('host')).strip(), str(self.settings.get('community'))
        try:
            ap = snmp.walk(host, community, BSN_AP)
            ifs = snmp.walk(host, community, BSN_AP_IF)
            loads = snmp.walk(host, community, BSN_AP_LOAD)
        except (snmp.SnmpError, OSError) as exc:
            raise PluginError(str(exc)) from exc

        def column(table, col):
            # suffix "col.m1.m2.m3.m4.m5.m6[.slot]" -> {index after col: value}
            return {k[len(col) + 1:]: v for k, v in table.items() if k.startswith(col + '.')}

        names, status, models = (column(ap, c) for c in (BSN_AP_NAME, BSN_AP_STATUS, BSN_AP_MODEL))
        types, channels, opers, users = (column(ifs, c) for c in (BSN_IF_TYPE, BSN_IF_CHANNEL, BSN_IF_OPER,
                                                                   BSN_IF_USERS))
        util, load_clients = column(loads, BSN_LOAD_CHANNEL_UTIL), column(loads, BSN_LOAD_CLIENTS)
        radios = {}
        for index, channel in channels.items():
            mac, _, _slot = index.rpartition('.')
            if opers.get(index) != BSN_IF_UP:
                continue
            clients = load_clients.get(index, users.get(index)) or 0
            radios.setdefault(mac, []).append({
                'band': BSN_IF_BANDS.get(types.get(index)) or _band_from_channel(channel),
                'channel': channel, 'width': None, 'utilization': util.get(index), 'clients': clients})
        out = []
        for mac, raw in names.items():
            name = raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else str(raw or '')
            if not name.strip():
                continue
            model = models.get(mac)
            model = model.decode('utf-8', 'replace') if isinstance(model, bytes) else str(model or '')
            rs = radios.get(mac, [])
            out.append({'name': name, 'model': model,
                        'status': 'up' if status.get(mac) == BSN_ASSOCIATED else 'down',
                        'clients': sum(r['clients'] for r in rs), 'radios': rs})
        return out
