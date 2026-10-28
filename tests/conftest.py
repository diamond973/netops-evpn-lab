"""Shared fixtures and helpers. Everything is derived from inventory/fabric.yml."""

import json
import subprocess
import time
from pathlib import Path

import pytest
import yaml

FABRIC_FILE = Path(__file__).resolve().parent.parent / "inventory" / "fabric.yml"
FABRIC = yaml.safe_load(FABRIC_FILE.read_text())

LEAFS = FABRIC["leafs"]
SPINES = FABRIC["spines"]
HOSTS = FABRIC["hosts"]
VLANS = {v["id"]: v for v in FABRIC["vlans"]}
VRFS = {v["name"]: v for v in FABRIC.get("vrfs", [])}
LOOPBACKS = {n["name"]: n["loopback"] for n in SPINES + LEAFS}


def container(node: str) -> str:
    return f"clab-{FABRIC['lab_name']}-{node}"


def sh(node: str, *cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "exec", container(node), *cmd],
        capture_output=True, text=True, check=check, timeout=60,
    )


def vtysh_json(node: str, command: str):
    out = sh(node, "vtysh", "-c", f"{command} json").stdout
    return json.loads(out) if out.strip() else {}


def ping(src: str, dst_ip: str, count: int = 3) -> bool:
    res = sh(src, "ping", "-c", str(count), "-W", "1", dst_ip, check=False)
    return res.returncode == 0


def fabric_ifaces(node: str) -> list[str]:
    """Underlay interfaces of a node, from fabric_links."""
    out = []
    for link in FABRIC["fabric_links"]:
        for end in link:
            name, ifname = end.split(":")
            if name == node:
                out.append(ifname)
    return out


def leafs_with_vlan(vlan_id: int) -> set[str]:
    return {h["leaf"] for h in HOSTS if h["vlan"] == vlan_id}


def evpn_paths(node: str, route_type: str):
    """Yield (rd, prefix, best_path) for every EVPN route of a type."""
    data = vtysh_json(node, f"show bgp l2vpn evpn route type {route_type}")
    for rd, routes in data.items():
        if not isinstance(routes, dict):
            continue
        for prefix, route in routes.items():
            if prefix == "rd" or not isinstance(route, dict):
                continue
            for path_group in route.get("paths", []):
                for path in path_group:
                    yield rd, prefix, path


def eventually(check, timeout: float = 90, interval: float = 3):
    """Retry `check` until it stops raising AssertionError (fabric convergence)."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            return check()
        except AssertionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(interval)


@pytest.fixture(scope="session")
def host_macs():
    return {
        h["name"]: sh(h["name"], "cat", "/sys/class/net/eth1/address").stdout.strip()
        for h in HOSTS
    }


@pytest.fixture(scope="session", autouse=True)
def lab_running():
    for node in LOOPBACKS:
        res = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container(node)],
            capture_output=True, text=True,
        )
        if res.stdout.strip() != "true":
            pytest.exit(f"{container(node)} is not running, run `make deploy` first", 2)
