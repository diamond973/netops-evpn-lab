"""Underlay: eBGP unnumbered sessions and loopback reachability."""

import pytest

from conftest import LEAFS, LOOPBACKS, SPINES, eventually, fabric_ifaces, sh, vtysh_json

NODES = [n["name"] for n in SPINES + LEAFS]


@pytest.mark.parametrize("node", NODES)
@pytest.mark.parametrize("afi", ["ipv4Unicast", "l2VpnEvpn"])
def test_bgp_sessions_established(node, afi):
    expected = set(fabric_ifaces(node))

    def check():
        peers = vtysh_json(node, "show bgp summary").get(afi, {}).get("peers", {})
        assert set(peers) == expected, f"{node} {afi}: peers {sorted(peers)}"
        down = {p: v["state"] for p, v in peers.items() if v["state"] != "Established"}
        assert not down, f"{node} {afi}: not Established: {down}"

    eventually(check)


@pytest.mark.parametrize("leaf", [n["name"] for n in LEAFS])
def test_leaf_loopbacks_in_rib_with_ecmp(leaf):
    """Every other loopback is a BGP route; remote leaf loopbacks use both spines."""
    def check():
        rib = vtysh_json(leaf, "show ip route bgp")
        for name, lo in LOOPBACKS.items():
            if name == leaf:
                continue
            assert f"{lo}/32" in rib, f"{leaf} has no route to {name} {lo}/32"
        for other in LEAFS:
            if other["name"] == leaf:
                continue
            route = rib[f"{other['loopback']}/32"][0]
            active = [nh for nh in route["nexthops"] if nh.get("active")]
            assert len(active) == len(SPINES), f"{leaf}->{other['name']}: {len(active)} next hops"

    eventually(check)


@pytest.mark.parametrize("leaf", [n["name"] for n in LEAFS])
def test_vtep_loopbacks_reachable(leaf):
    lo = LOOPBACKS[leaf]
    for other in LEAFS:
        if other["name"] != leaf:
            res = sh(leaf, "ping", "-c", "2", "-W", "1", "-I", lo, other["loopback"], check=False)
            assert res.returncode == 0, f"{leaf} cannot reach VTEP {other['loopback']}"
