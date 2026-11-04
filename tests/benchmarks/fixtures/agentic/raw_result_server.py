"""Synthetic MCP JSON-RPC peer: deliberately bypass server-side result validation."""

import json
import sys
import time


def token(kind):
    return {
        "literal": "synthetic_bootstrap_opaque",
        "bound-shaped": "AIza" + "q" * 35,
        "lexical": "AIza" + "q" * 35,
        "clean": "clean-value",
    }[kind]


def response(method, params, case, value):
    if method == "initialize":
        result = {
            "protocolVersion": params["protocolVersion"],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "synthetic", "version": "1"},
        }
        if case == "handshake":
            result["discarded"] = value
        return {"result": result}
    if method == "tools/list":
        result = {
            "tools": [
                {
                    "name": "search_docs",
                    "inputSchema": {"type": "object"},
                    "outputSchema": {"type": "object", "required": ["answer"]},
                }
            ]
        }
        if case == "listing":
            result["discarded"] = value
        return {"result": result}
    result = {"content": [{"type": "text", "text": "safe"}]}
    if case == "missing-structured":
        result["content"][0]["text"] = value
    elif case == "invalid-structured":
        result["structuredContent"] = {"hidden": value}
    elif case == "malformed-content":
        result["content"] = [{"type": "unknown", "hidden": value}]
    elif case in ("dropped-field", "escaped-field"):
        result["discarded"] = value
    elif case == "error-result":
        result.update(isError=True, discarded=value)
    elif case == "rpc-error":
        return {"error": {"code": -32603, "message": "clean error", "data": {"hidden": value}}}
    elif case == "dropped-error":
        return {"error": {"code": -32603, "message": "clean error", "discarded": value}}
    elif case == "malformed-envelope":
        return {"result": ["invalid result", {"hidden": value}]}
    elif case == "rpc-clean":
        return {"error": {"code": -32603, "message": "clean error"}}
    elif case == "valid":
        result["structuredContent"] = {"answer": "clean answer"}
    return {"result": result}


if __name__ == "__main__":
    case, kind = sys.argv[1:]
    # The literal deliberately has no lexical-key match; its exact bytes matter.
    value = (
        "prefix" + token(kind) + "-suffix" if kind in ("literal", "bound-shaped") else token(kind)
    )
    print("synthetic server diagnostic retained", file=sys.stderr, flush=True)
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        if case == "wait" and request["method"] == "tools/call":
            time.sleep(30)  # Cancellation must stop only this owned subprocess.
        if case == "oversize" and request["method"] == "tools/call":
            print("x" * 2048, flush=True)
            continue
        message = {
            "jsonrpc": "2.0",
            "id": request["id"],
            **response(request["method"], request.get("params", {}), case, value),
        }
        encoded = json.dumps(message)
        if case == "escaped-field":
            encoded = encoded.replace(token(kind), "".join(f"\\u{ord(c):04x}" for c in token(kind)))
        if (
            case in ("trailing-encoded", "unfinished-encoded", "broken-encoded")
            and request["method"] == "tools/call"
        ):
            # Complete envelope, then a string with a single encoded character.
            hidden = value.replace(token(kind)[0], f"\\u{ord(token(kind)[0]):04x}", 1)
            encoded += ' "note":"' + hidden
            if case == "trailing-encoded":
                encoded += '"'
            elif case == "broken-encoded":
                encoded += "\\u00"
        print(encoded, flush=True)
