#!/usr/bin/env python3
"""Minimal Auphonic API client (Python stdlib only) for master_sources.py and master_mix.py.

usage (diagnostics, never prints the key):
  python3 auphonic_api.py --check        # is a key available? remaining credits (hours)
  python3 auphonic_api.py --presets      # your presets: name, UUID (for --preset / $AUPHONIC_PRESET), outputs
                                         # ("OK for master_sources" = exactly one wav-24bit output, not a mono mixdown)

API KEY: read at run time, in this order, and never printed, logged or written to any receipt:
  1. $AUPHONIC_API_KEY
  2. the macOS Keychain, service "auphonic-api-key"
     store it once:  security add-generic-password -U -a "$USER" -s auphonic-api-key -w '<key>'

Hard-won API notes (see references/audio.md):
  * The download URL answers with a redirect to S3, and S3 REJECTS the Authorization header: fetch the
    redirect target without auth (download() does this).
  * loudnesstarget must be an integer from Auphonic's list (-16, not -16.0); do not send bitrate as a string.
  * Production status 3 = done; 2 or 11 (or a status_string starting with "Error") = failed.
  * Check credits (hours) before submitting; each production also bills a small per-file minimum.
"""
import argparse, json, os, subprocess, sys, time, urllib.error, urllib.request, uuid as _uuid

API = "https://auphonic.com/api"
KEYCHAIN_SERVICE = "auphonic-api-key"
TERMINAL_ERRORS = (2, 11)


class AuphonicError(RuntimeError):
    pass


def api_key():
    """The API key from $AUPHONIC_API_KEY or the macOS Keychain; raises AuphonicError when absent."""
    k = (os.environ.get("AUPHONIC_API_KEY") or "").strip()
    if not k:
        for extra in (["-a", os.environ.get("USER", "")], []):
            try:
                k = subprocess.run(["security", "find-generic-password", *extra, "-s", KEYCHAIN_SERVICE, "-w"],
                                   capture_output=True, text=True).stdout.strip()
            except FileNotFoundError:  # not macOS
                k = ""
            if k:
                break
    if not k:
        raise AuphonicError("No Auphonic API key: export AUPHONIC_API_KEY or store it in the macOS Keychain: "
                            "security add-generic-password -U -a \"$USER\" -s auphonic-api-key -w '<key>'")
    return k


def req(method, path, key, data=None, headers=None, raw=False, timeout=120):
    url = path if path.startswith("http") else API + path
    h = {"Authorization": f"bearer {key}"}
    h.update(headers or {})
    if isinstance(data, (dict, list)):
        data = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h, method=method), timeout=timeout)
    except urllib.error.HTTPError as e:
        raise AuphonicError(f"Auphonic API {method} {path} -> HTTP {e.code}: {e.read()[:600].decode(errors='replace')}")
    except urllib.error.URLError as e:
        raise AuphonicError(f"Auphonic API {method} {path} -> {e.reason}")
    body = r.read()
    return body if raw else json.loads(body)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def download(key, url, dest):
    """Auphonic answers with a redirect to S3; S3 rejects the Authorization header, so follow it without auth."""
    try:
        r = urllib.request.build_opener(_NoRedirect).open(
            urllib.request.Request(url, headers={"Authorization": f"bearer {key}"}), timeout=120)
        data = r.read()
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308):
            raise AuphonicError(f"Auphonic download -> HTTP {e.code}: {e.read()[:300]}")
        loc = e.headers["Location"]
        data = urllib.request.urlopen(urllib.request.Request(loc), timeout=1800).read()
    tmp = dest + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dest)


def upload(key, prod, path):
    boundary = "----auphonic" + _uuid.uuid4().hex
    name = os.path.basename(path)
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"input_file\"; filename=\"{name}\"\r\n"
            f"Content-Type: audio/wav\r\n\r\n").encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    with open(path, "rb") as f:
        body = head + f.read() + tail
    return req("POST", f"/production/{prod}/upload.json", key, data=body,
               headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, timeout=1800)


def credits(key):
    """Remaining credits in hours (None if the API does not say)."""
    return req("GET", "/user.json", key)["data"].get("credits")


def create_production(key, body):
    return req("POST", "/productions.json", key, data=body)["data"]["uuid"]


def start(key, prod):
    return req("POST", f"/production/{prod}/start.json", key, data=b"")


def wait(key, prod, poll=20, log=None):
    """Poll until done; returns the production data. Raises AuphonicError on failure."""
    while True:
        d = req("GET", f"/production/{prod}.json", key)["data"]
        if log:
            log(f"{prod} status {d.get('status_string') or d.get('status')}")
        if d.get("status") == 3:
            return d
        if d.get("status") in TERMINAL_ERRORS or str(d.get("status_string", "")).lower().startswith("error"):
            raise AuphonicError(f"Auphonic failed: {d.get('error_message') or d.get('status_string')}")
        time.sleep(poll)


def presets(key):
    """[(name, uuid, outputs, ok_for_master_sources)] of the account's presets (GET /presets.json)."""
    rows = []
    for p in req("GET", "/presets.json", key).get("data") or []:
        outs = p.get("output_files") or []
        wav24 = [o for o in outs if o.get("format") == "wav-24bit" and not o.get("mono_mixdown")]
        desc = ", ".join("%s%s" % (o.get("format") or o.get("ending") or "?", " (mono mixdown)" if o.get("mono_mixdown") else "")
                         for o in outs) or "no outputs"
        rows.append((p.get("preset_name") or "?", p.get("uuid") or "?", desc, len(wav24) == 1))
    return rows


def level_stats(d):
    """Auphonic's own input/output level statistics, as stored in receipts."""
    st = (d.get("statistics") or {}).get("levels", {})
    return ({k: st.get("input", {}).get(k) for k in ("loudness", "signal_level", "noise_level", "snr", "lra")},
            {k: st.get("output", {}).get(k) for k in ("loudness", "lra", "peak", "gain_mean", "gain_min", "gain_max")})


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="verify a key is available and print remaining credits")
    ap.add_argument("--presets", action="store_true",
                    help="list your presets with their UUIDs (the value for --preset / $AUPHONIC_PRESET / auphonic.preset)")
    a = ap.parse_args()
    if not (a.check or a.presets):
        ap.print_help()
        sys.exit(0)
    try:
        k = api_key()
        if a.check:
            print("API key: found (not shown)")
            print(f"credits: {credits(k)} h")
        if a.presets:
            rows = presets(k)
            if not rows:
                print("no presets: create one at auphonic.com (Presets), with exactly one WAV 24-bit output")
            for name, uid, desc, ok in rows:
                print(f"{uid}  {name}  [{desc}]{'  OK for master_sources' if ok else ''}")
    except AuphonicError as e:
        sys.exit(str(e))
