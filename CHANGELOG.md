# Changelog

Each release has a section `## <version>`; its text becomes the GitHub release notes and the
Marketplace's "what's new".

## 1.1.0

- Each access point goes into the inventory with its model, serial and address (role access-point),
  so it can be placed on the floor plan without typing it in. An AP is known by its MAC: renaming it
  on the controller keeps it.
- Channel widths a 9800 sends as a name (`chan-width-80-mhz`) are read as well as plain numbers.

## 1.0.0

- First release: access points from a Cisco Catalyst 9800 (RESTCONF) or AireOS controller (SNMP v2c)
  for the Wi-Fi view.
