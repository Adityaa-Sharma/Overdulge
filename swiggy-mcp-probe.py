#!/usr/bin/env python3
"""
MCP feasibility probe for Swiggy (Food/Instamart) and Zepto.

Checks, WITHOUT authentication (safe to run, read-only):
  1. Whether the MCP endpoint is alive and speaks streamable HTTP JSON-RPC.
  2. OAuth discovery: protected-resource metadata and authorization-server
     metadata (RFC 9728 / RFC 8414 well-known endpoints).
  3. Whether Dynamic Client Registration (DCR) is supported
     (registration_endpoint present) -> determines if we can register our
     Cloudflare Worker callback programmatically vs needing manual whitelisting.
  4. Supported grant types / PKCE methods.
  5. Whether tools/list is callable without auth (some servers allow schema
     discovery pre-auth). If yes, dumps the full tool list -> this answers
     the Swiggy order-history question immediately.

Usage:
    pip install requests
    python probe_mcp_feasibility.py
"""

import json
import sys
from urllib.parse import urlparse

import requests

TIMEOUT = 15
SERVERS = {
    "swiggy-food": "https://mcp.swiggy.com/food",
    "swiggy-instamart": "https://mcp.swiggy.com/im",
    "zepto": "https://mcp.zepto.co.in/mcp",
}

HEADERS_JSON = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "User-Agent": "mcp-feasibility-probe/0.1",
}


def get_json(url):
    try:
        r = requests.get(url, timeout=TIMEOUT, headers={"Accept": "application/json"})
        if r.status_code == 200:
            try:
                return r.status_code, r.json()
            except ValueError:
                return r.status_code, None
        return r.status_code, None
    except requests.RequestException as e:
        return None, str(e)


def post_jsonrpc(url, method, params=None, session_id=None, extra_headers=None):
    headers = dict(HEADERS_JSON)
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    if extra_headers:
        headers.update(extra_headers)
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT)
        return r
    except requests.RequestException as e:
        print(f"    ! request failed: {e}")
        return None


def parse_sse_or_json(resp):
    """MCP streamable HTTP may answer JSON or SSE."""
    ctype = resp.headers.get("Content-Type", "")
    if "text/event-stream" in ctype:
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                try:
                    return json.loads(line[len("data:"):].strip())
                except ValueError:
                    continue
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def probe_oauth_metadata(base_url):
    """Try RFC 9728 protected-resource metadata and RFC 8414 AS metadata."""
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")

    findings = {}

    # Protected resource metadata: path-aware and root variants
    pr_candidates = [
        f"{origin}/.well-known/oauth-protected-resource{path}",
        f"{origin}{path}/.well-known/oauth-protected-resource",
        f"{origin}/.well-known/oauth-protected-resource",
    ]
    for u in pr_candidates:
        status, body = get_json(u)
        if status == 200 and isinstance(body, dict):
            findings["protected_resource_metadata_url"] = u
            findings["protected_resource_metadata"] = body
            break

    # Authorization server(s): from PR metadata if present, else guess origin
    as_urls = []
    prm = findings.get("protected_resource_metadata") or {}
    as_urls.extend(prm.get("authorization_servers", []))
    if not as_urls:
        as_urls.append(origin)

    for as_base in as_urls:
        as_parsed = urlparse(as_base)
        as_origin = f"{as_parsed.scheme}://{as_parsed.netloc}"
        as_path = as_parsed.path.rstrip("/")
        as_candidates = [
            f"{as_origin}/.well-known/oauth-authorization-server{as_path}",
            f"{as_origin}{as_path}/.well-known/oauth-authorization-server",
            f"{as_origin}/.well-known/oauth-authorization-server",
            f"{as_origin}/.well-known/openid-configuration",
        ]
        for u in as_candidates:
            status, body = get_json(u)
            if status == 200 and isinstance(body, dict) and "authorization_endpoint" in body:
                findings["auth_server_metadata_url"] = u
                findings["auth_server_metadata"] = body
                return findings
    return findings


def summarize_auth_server(meta):
    print(f"    authorization_endpoint : {meta.get('authorization_endpoint')}")
    print(f"    token_endpoint         : {meta.get('token_endpoint')}")
    reg = meta.get("registration_endpoint")
    if reg:
        print(f"    registration_endpoint  : {reg}")
        print("    >>> DCR SUPPORTED — our Worker can likely self-register its")
        print("        callback URL. Manual whitelisting may not be needed.")
    else:
        print("    registration_endpoint  : ABSENT")
        print("    >>> No DCR — redirect URI whitelisting request is REQUIRED")
        print("        for any non-localhost deployment.")
    print(f"    grant_types_supported  : {meta.get('grant_types_supported')}")
    print(f"    code_challenge_methods : {meta.get('code_challenge_methods_supported')}")
    print(f"    scopes_supported       : {meta.get('scopes_supported')}")


def probe_server(name, url):
    print(f"\n{'=' * 70}\n{name}  ->  {url}\n{'=' * 70}")

    # 1) Unauthenticated initialize
    resp = post_jsonrpc(url, "initialize", {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "feasibility-probe", "version": "0.1"},
    })
    session_id = None
    initialized_ok = False
    if resp is None:
        print("  [1] initialize: unreachable")
    else:
        print(f"  [1] initialize -> HTTP {resp.status_code}")
        www = resp.headers.get("WWW-Authenticate")
        if www:
            print(f"      WWW-Authenticate: {www}")
        if resp.status_code == 200:
            body = parse_sse_or_json(resp)
            session_id = resp.headers.get("Mcp-Session-Id")
            initialized_ok = body is not None and "result" in (body or {})
            if initialized_ok:
                server_info = body["result"].get("serverInfo", {})
                print(f"      server: {server_info.get('name')} {server_info.get('version')}")
                print("      >>> initialize allowed WITHOUT auth")
        elif resp.status_code in (401, 403):
            print("      >>> auth required before initialize (expected)")

    # 2) Unauthenticated tools/list (only meaningful if initialize passed)
    if initialized_ok:
        post_jsonrpc(url, "notifications/initialized", {}, session_id)
        resp2 = post_jsonrpc(url, "tools/list", {}, session_id)
        if resp2 is not None and resp2.status_code == 200:
            body2 = parse_sse_or_json(resp2)
            tools = (body2 or {}).get("result", {}).get("tools", [])
            print(f"  [2] tools/list -> {len(tools)} tools WITHOUT auth:")
            history_hits = []
            for t in tools:
                tname = t.get("name", "?")
                tdesc = (t.get("description") or "").strip().split("\n")[0][:90]
                print(f"      - {tname}: {tdesc}")
                blob = (tname + " " + (t.get("description") or "")).lower()
                if any(k in blob for k in ("history", "past order", "previous order", "my orders", "order_list", "orders")):
                    history_hits.append(tname)
            if history_hits:
                print(f"      >>> ORDER-HISTORY CANDIDATES: {history_hits}")
            else:
                print("      >>> No order-history-looking tool found unauthenticated.")
        else:
            code = resp2.status_code if resp2 is not None else "n/a"
            print(f"  [2] tools/list -> HTTP {code} (auth likely required for tools)")
    else:
        print("  [2] tools/list skipped (initialize not permitted unauthenticated)")
        print("      -> use Claude Desktop / Cursor for the authenticated tool dump.")

    # 3) OAuth discovery
    print("  [3] OAuth discovery:")
    findings = probe_oauth_metadata(url)
    if "protected_resource_metadata" in findings:
        print(f"      protected-resource metadata at {findings['protected_resource_metadata_url']}")
        prm = findings["protected_resource_metadata"]
        print(f"      authorization_servers: {prm.get('authorization_servers')}")
        print(f"      scopes_supported     : {prm.get('scopes_supported')}")
    else:
        print("      no protected-resource metadata found (server may embed AS info in 401 header)")
    if "auth_server_metadata" in findings:
        print(f"      auth-server metadata at {findings['auth_server_metadata_url']}")
        summarize_auth_server(findings["auth_server_metadata"])
    else:
        print("      no auth-server metadata discovered at well-known paths")

    return findings


def main():
    print("MCP feasibility probe — Swiggy & Zepto (unauthenticated checks only)")
    results = {}
    for name, url in SERVERS.items():
        try:
            results[name] = probe_server(name, url)
        except Exception as e:
            print(f"  ! probe crashed for {name}: {e}")

    print(f"\n{'=' * 70}\nNEXT STEP\n{'=' * 70}")
    print("For the authenticated tool dump (the order-history verdict):")
    print("  Claude Desktop -> Settings -> Connectors -> Add custom connector")
    print("    https://mcp.swiggy.com/food   (login with OTP)")
    print("    https://mcp.swiggy.com/im")
    print("    https://mcp.zepto.co.in/mcp")
    print('  Then ask: "List every available tool from each connected server')
    print('  with its exact name, description, and input schema. Do not call')
    print('  any tool that places orders or modifies a cart."')


if __name__ == "__main__":
    main()