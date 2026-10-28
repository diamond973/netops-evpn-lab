"""Overlay: VNIs, EVPN type-2/3/5 routes, host reachability and VLAN isolation."""

import pytest

from conftest import (
    HOSTS, LEAFS, LOOPBACKS, VLANS, VRFS, FABRIC,
    eventually, evpn_paths, leafs_with_vlan, ping, sh, vtysh_json,
)

LEAF_NAMES = [n["name"] for n in LEAFS]
RT = FABRIC["rt_prefix"]


@pytest.mark.parametrize("leaf", LEAF_NAMES)
def test_vnis_up(leaf):
    vnis = vtysh_json(leaf, "show evpn vni")
    for vlan in VLANS.values():
        info = vnis.get(str(vlan["vni"]))
        assert info, f"{leaf}: L2 VNI {vlan['vni']} missing"
        assert info["type"] == "L2"
        assert info["vxlanIf"] == f"vni{vlan['vni']}"
        if "vrf" in vlan:
            assert info["tenantVrf"] == vlan["vrf"]
        link = sh(leaf, "ip", "-o", "link", "show", f"vni{vlan['vni']}").stdout
        assert ",UP" in link or "<UP" in link, f"{leaf}: vni{vlan['vni']} is down"
    for vrf in VRFS.values():
        assert vnis.get(str(vrf["l3vni"]), {}).get("type") == "L3", \
            f"{leaf}: L3 VNI {vrf['l3vni']} missing"
        info = vtysh_json(leaf, f"show evpn vni {vrf['l3vni']}")
        assert info["state"] == "Up", f"{leaf}: L3 VNI {vrf['l3vni']} is {info['state']}"
        assert info["tenantVrf"] == vrf["name"]
        members = {v["vni"] for v in VLANS.values() if v.get("vrf") == vrf["name"]}
        assert members <= set(info["l2Vnis"]), f"{leaf}: L2 VNIs not bound to {vrf['name']}"


@pytest.mark.parametrize("leaf", LEAF_NAMES)
@pytest.mark.parametrize("vlan_id", list(VLANS))
def test_type3_imet_from_remote_vteps(leaf, vlan_id):
    """Each leaf sees an inclusive-multicast route from every other leaf in the VNI."""
    vni = VLANS[vlan_id]["vni"]
    expected = {LOOPBACKS[l] for l in leafs_with_vlan(vlan_id)} - {LOOPBACKS[leaf]}

    def check():
        seen = {
            path["ip"] for _, _, path in evpn_paths(leaf, "multicast")
            if f"RT:{RT}:{vni}" in path["extendedCommunity"]["string"]
        }
        assert expected <= seen, f"{leaf} VNI {vni}: type-3 from {seen}, want {expected}"
        flood = vtysh_json(leaf, f"show evpn vni {vni}")
        vteps = {v["ip"] for v in flood.get("remoteVteps", [])}
        assert expected <= vteps, f"{leaf} VNI {vni}: flood list {vteps}"

    eventually(check)


@pytest.mark.parametrize("host", HOSTS, ids=[h["name"] for h in HOSTS])
def test_type2_macip_on_remote_leafs(host, host_macs):
    """Each host's MAC and MAC+IP are advertised to every other leaf with the right RT/next hop."""
    mac = host_macs[host["name"]]
    vni = VLANS[host["vlan"]]["vni"]
    origin = LOOPBACKS[host["leaf"]]
    sh(host["name"], "ping", "-c", "1", "-W", "1", VLANS[host["vlan"]].get("gateway", host["ip"]),
       check=False)

    for leaf in LEAF_NAMES:
        if leaf == host["leaf"]:
            continue

        def check():
            paths = [p for _, _, p in evpn_paths(leaf, "macip") if p.get("mac") == mac]
            assert paths, f"{leaf}: no type-2 for {host['name']} {mac}"
            assert all(p["nexthops"][0]["ip"] == origin for p in paths)
            assert all(f"RT:{RT}:{vni}" in p["extendedCommunity"]["string"] for p in paths)
            ips = {p.get("ip") for p in paths}
            assert host["ip"] in ips, f"{leaf}: no MAC+IP route for {host['ip']} (have {ips})"
            fdb = sh(leaf, "bridge", "fdb", "show", "dev", f"vni{vni}").stdout
            assert f"{mac} dst {origin}" in fdb, f"{leaf}: {mac} not in VXLAN FDB"

        eventually(check)


def _cross_leaf_pairs(same_vlan: bool):
    pairs = []
    for a in HOSTS:
        for b in HOSTS:
            if a["leaf"] != b["leaf"] and (a["vlan"] == b["vlan"]) == same_vlan:
                pairs.append(pytest.param(a, b, id=f"{a['name']}->{b['name']}"))
    return pairs


@pytest.mark.parametrize("src,dst", _cross_leaf_pairs(same_vlan=True))
def test_l2_ping_same_vlan_across_leafs(src, dst):
    eventually(lambda: _assert_ping(src, dst), timeout=30)


def _routed(a, b):
    va, vb = VLANS[a["vlan"]], VLANS[b["vlan"]]
    return "vrf" in va and va.get("vrf") == vb.get("vrf")


@pytest.mark.parametrize("src,dst", [p for p in _cross_leaf_pairs(same_vlan=False)
                                     if _routed(*p.values)])
def test_l3_ping_symmetric_irb_across_leafs(src, dst):
    """Inter-VLAN traffic in the tenant VRF is routed through the L3 VNI (TTL drops by 2)."""
    eventually(lambda: _assert_ping(src, dst), timeout=30)
    out = sh(src["name"], "ping", "-c", "1", "-W", "1", dst["ip"]).stdout
    assert "ttl=62" in out, f"expected two routed hops, got: {out}"


@pytest.mark.parametrize("leaf", LEAF_NAMES)
@pytest.mark.parametrize("vrf", list(VRFS))
def test_remote_host_routes_via_l3vni(leaf, vrf):
    rib = vtysh_json(leaf, f"show ip route vrf {vrf} bgp")
    for h in HOSTS:
        if h["leaf"] == leaf or VLANS[h["vlan"]].get("vrf") != vrf:
            continue
        route = rib.get(f"{h['ip']}/32")
        assert route, f"{leaf}: no /32 for {h['name']} in VRF {vrf}"
        nh = route[0]["nexthops"][0]
        assert nh["ip"] == LOOPBACKS[h["leaf"]]
        assert nh["interfaceName"] == f"br{VRFS[vrf]['l3vni']}"


def _assert_ping(src, dst):
    assert ping(src["name"], dst["ip"]), f"{src['name']} cannot reach {dst['name']} {dst['ip']}"


@pytest.mark.parametrize("src,dst", _cross_leaf_pairs(same_vlan=False)
                         + [pytest.param(a, b, id=f"{a['name']}->{b['name']}")
                            for a in HOSTS for b in HOSTS
                            if a["leaf"] == b["leaf"] and a["vlan"] != b["vlan"]])
def test_vlans_isolated_at_l2(src, dst):
    """Negative test: a host in another VLAN is unreachable at L2.

    dst temporarily gets an extra address inside src's subnet. If VLANs leaked
    into each other, src would ARP for it directly and get an answer. With
    proper isolation the ARP never reaches dst and the ping fails.
    """
    vlan = VLANS[src["vlan"]]
    plen = vlan["subnet"].split("/")[1]
    probe = vlan["subnet"].split("/")[0].rsplit(".", 1)[0] + ".250"
    sh(src["name"], "ip", "neigh", "flush", probe, check=False)
    sh(dst["name"], "ip", "addr", "add", f"{probe}/{plen}", "dev", "eth1")
    try:
        assert not ping(src["name"], probe, count=2), (
            f"{src['name']} (VLAN {src['vlan']}) reached {dst['name']} "
            f"(VLAN {dst['vlan']}) at L2 via {probe}"
        )
        arp = sh(src["name"], "ip", "neigh", "show", probe, check=False).stdout
        assert "lladdr" not in arp or "FAILED" in arp or "INCOMPLETE" in arp
    finally:
        sh(dst["name"], "ip", "addr", "del", f"{probe}/{plen}", "dev", "eth1", check=False)
        sh(src["name"], "ip", "neigh", "flush", probe, check=False)
