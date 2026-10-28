# netops-evpn-lab

A small datacenter fabric built and tested as code: BGP EVPN / VXLAN on FRR,
deployed with Containerlab and Ansible, verified by pytest, run end-to-end in CI.

One YAML file describes the fabric. Everything else (topology, router configs,
Linux bridge/VXLAN setup, tests) is generated from it.

## Topology

```
          spine1 (AS65100)      spine2 (AS65100)
             |      \          /      |
             |       \        /       |
             |        \      /        |
          leaf1 (AS65101)      leaf2 (AS65102)
          VTEP 10.0.0.11       VTEP 10.0.0.12
           |        |           |        |
         host1    host2       host3    host4
        VLAN10   VLAN20      VLAN10   VLAN20
```

- **Underlay:** eBGP unnumbered over IPv6 link-local, no IPs on fabric links, ECMP to every loopback.
- **Overlay:** BGP EVPN, VXLAN L2 VNIs per VLAN (10010, 10020).
- **Routing between VLANs:** symmetric IRB with an L3 VNI (50000) in VRF `tenant1`,
  anycast gateway with the same IP and MAC on every leaf.
- **Images:** `quay.io/frrouting/frr:10.5.5` for the switches, Alpine for hosts.

## How it works

| Step | What happens |
|---|---|
| `inventory/fabric.yml` | Single source of truth: nodes, ASNs, links, VLANs, VNIs, VRFs, hosts |
| `make render` | Ansible renders the Containerlab topology, `frr.conf` per node and the Linux netdev scripts into `build/` |
| `make deploy` | Starts the lab if the topology changed, pushes configs only where they differ (idempotent, `changed` only on real change) |
| `make test` | pytest checks the fabric through `vtysh ... json` and real pings |

To add a VLAN: add it to `fabric.yml`, run `make deploy`, then `make test`. No redeploy of the lab needed.

## Tests (40)

**Underlay**
- IPv4 unicast and L2VPN EVPN BGP sessions established on every node
- Each leaf sees the other leaf loopbacks via both spines (ECMP)
- VTEP loopbacks reachable

**Overlay**
- VNIs are up on every leaf
- Type-3 (IMET) routes received from remote VTEPs for each VLAN
- Type-2 (MAC/IP) routes for every host on the remote leaf
- Same-VLAN ping across leafs (L2 over VXLAN)
- Cross-VLAN ping across leafs (symmetric IRB)
- Remote host routes installed in the VRF via the L3 VNI
- Hosts in different VLANs cannot reach each other at L2

## Run it

Requirements: Linux, Docker, [Containerlab](https://containerlab.dev), Python 3.

```bash
make deploy   # creates .venv, renders configs, starts the lab, pushes configs
make test     # 40 tests, about a minute
make destroy  # stop the lab
```
