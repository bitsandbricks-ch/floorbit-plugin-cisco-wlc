# Cisco WLC for Floorbit

[![Test and release](https://github.com/bitsandbricks-ch/floorbit-plugin-cisco-wlc/actions/workflows/release.yml/badge.svg?branch=main)](https://github.com/bitsandbricks-ch/floorbit-plugin-cisco-wlc/actions/workflows/release.yml?query=branch%3Amain)

The `cisco-wlc` plugin for [Floorbit](https://github.com/bitsandbricks-ch/floorbit): access points from a Cisco wireless controller for the Wi-Fi view (status, and per radio the channel, width, channel use and client count), and each AP in the inventory (role access-point, with its model, serial and address), ready to place on the floor plan.
Install it under Settings > Plugins > Marketplace; how to set it up is in Floorbit's
[docs/plugins.md](https://github.com/bitsandbricks-ch/floorbit/blob/master/docs/plugins.md).

## Controllers

| Controller | How it reads | Settings |
| --- | --- | --- |
| Catalyst 9800 (IOS-XE 17) | RESTCONF: `access-point-oper-data` (`capwap-data`, `radio-oper-data`) and `rrm-oper-data/rrm-measurement` | URL, username, password. Turn RESTCONF on (`restconf`, `ip http secure-server`). |
| AireOS (3504, 5520, 8540, Mobility Express) | SNMP v2c walks of AIRESPACE-WIRELESS-MIB: `bsnAPTable`, `bsnAPIfTable`, `bsnAPIfLoadParametersTable` | Management address and a read-only community. SNMP v3 is not supported yet. |

Only counts leave the hall: access point names, models, channels, channel use and client numbers,
never client names or MACs.

<!-- TODO(Mike): checked against Cisco's published YANG models and MIB, not yet against a real
controller. Run the Test button on one at the next event and report what it says. -->

## What is where

| File | What |
| --- | --- |
| `plugin.yaml` | The manifest: settings and what it may touch. |
| `plugin.py` | The plugin. Floorbit runs it on the local agent in the hall, which reaches the controller on the event network. |
| `snmp.py` | A small SNMP v2c client (GETBULK walks only) for AireOS, since Floorbit installs no SNMP library. |
| `tests/` | Tests against a fake 9800 and a fake SNMP agent, with Floorbit at the commit in `FLOORBIT_REF`. No network. |

## Testing

```
git clone https://github.com/bitsandbricks-ch/floorbit ../floorbit
git -C ../floorbit checkout "$(cat FLOORBIT_REF)"
pip install -r ../floorbit/requirements.txt pytest ruff
ruff check --config ../floorbit/pyproject.toml --config 'lint.isort.known-third-party=["floorbit"]' .
PYTHONPATH=../floorbit python -m pytest -q tests
```

## Releasing

Run `python scripts/changelog.py collect`, then `mason bump` (it sets the version in `plugin.yaml` and
turns `[Unreleased]` in `CHANGELOG.md` into its section), merge, then
push the tag (or `mason release tag floorbit-plugin-cisco-wlc`):

```
git tag v1.0.0 && git push origin v1.0.0
```

The [workflow](.github/workflows/release.yml) tests it, signs `cisco-wlc-<version>.tar.gz` with the
Floorbit plugin key (environment `plugin-signing`, secret `FLOORBIT_PLUGIN_SIGNING_KEY`) and
attaches it to the GitHub release. The Marketplace picks it up within the hour. A version is
withdrawn in [floorbit-plugins](https://github.com/bitsandbricks-ch/floorbit-plugins)' `plugins.yaml`,
never by deleting the release.
