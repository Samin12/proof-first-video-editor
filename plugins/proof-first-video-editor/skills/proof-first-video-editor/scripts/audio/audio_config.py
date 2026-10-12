#!/usr/bin/env python3
"""Project settings for the audio pipeline: a JSON config file and/or CLI arguments (no hard-coded paths).

usage (print the resolved settings):
  python3 audio_config.py [--config PATH] [--project ALIAS] [--bundle PATH] [--audio-dir DIR]
                          [--project-id ID] [--preset UUID] [--preset-name NAME]

Every audio script accepts the same project options (added with add_args()):
  --config PATH      JSON config. Default, first that exists: $PFVE_AUDIO_CONFIG, ./pfve-audio.json,
                     ~/.config/proof-first-video-editor/audio.json. Optional when --bundle is given.
  --project ALIAS    entry under "projects" in the config (default: the config's "default_project",
                     or the only entry).
  --bundle PATH      the Borumi project bundle (.bmprojbundle). Overrides the config entry; enough on its own.
  --audio-dir DIR    where mastered/ and stems/ are written. Default: "<bundle folder>/<bundle name> Agent/audio"
                     (the convention used next to the bundle), e.g. "My Video Agent/audio".
  --project-id ID    Borumi project id as shown by list_open_projects. Optional: when given it is verified
                     against the project opened from --bundle; when omitted the open project is matched by
                     bundle path (or name).
  --preset UUID      Auphonic preset UUID (default: config auphonic.preset, then $AUPHONIC_PRESET).
  --preset-name NAME label stored in receipts and track titles (default: config auphonic.preset_name,
                     e.g. "Broadcast Voice").

Other settings (config keys, environment overrides, defaults):
  borumi_mcp_dir             $BORUMI_MCP_DIR       ~/.borumi-agent/plugin/scripts  (opencutagent-borumi: borumi_mcp.py)
  borumi_builtin_sounds_dir  $BORUMI_BUILTIN_SOUNDS /Applications/Borumi.app/Contents/Resources
  edit_skill_dir             $BORUMI_EDIT_SKILL    ~/.borumi-edit/skill             (edit-borumi-video: finalize_export.py)
  loudness.target_lufs                             -16.0
  loudness.true_peak_dbtp                          -1.0

The Auphonic API KEY is never part of the config: it is read at run time from $AUPHONIC_API_KEY or the
macOS Keychain (see auphonic_api.py).

See config.example.json for the file format.
"""
import argparse, json, os, sys

DEFAULTS = {
    "borumi_mcp_dir": "~/.borumi-agent/plugin/scripts",
    "borumi_builtin_sounds_dir": "/Applications/Borumi.app/Contents/Resources",
    "edit_skill_dir": "~/.borumi-edit/skill",
    "target_lufs": -16.0,
    "true_peak_dbtp": -1.0,
}
CONFIG_CANDIDATES = (
    lambda: os.environ.get("PFVE_AUDIO_CONFIG"),
    lambda: os.path.join(os.getcwd(), "pfve-audio.json"),
    lambda: os.path.expanduser("~/.config/proof-first-video-editor/audio.json"),
)


def _x(p):
    return os.path.abspath(os.path.expanduser(p)) if p else p


def add_args(ap, bundle=True):
    """Add the shared project options to an argparse parser."""
    g = ap.add_argument_group("project / config")
    g.add_argument("--config", help="JSON config (default: $PFVE_AUDIO_CONFIG, ./pfve-audio.json, "
                                    "~/.config/proof-first-video-editor/audio.json)")
    g.add_argument("--project", help="project alias under 'projects' in the config")
    if bundle:
        g.add_argument("--bundle", help="Borumi .bmprojbundle (overrides the config)")
        g.add_argument("--audio-dir", help="audio work dir (default '<bundle folder>/<name> Agent/audio')")
        g.add_argument("--project-id", help="Borumi project id to verify against list_open_projects")
    g.add_argument("--preset", help="Auphonic preset UUID (default: config auphonic.preset / $AUPHONIC_PRESET)")
    g.add_argument("--preset-name", help="Auphonic preset label for receipts (e.g. 'Broadcast Voice')")
    return ap


def load_config(path=None):
    """Return (config dict, path) or ({}, None) when no config exists."""
    if path:
        p = _x(path)
        if not os.path.exists(p):
            raise SystemExit(f"config not found: {p}")
        return json.load(open(p)), p
    for cand in CONFIG_CANDIDATES:
        p = cand()
        if p and os.path.exists(_x(p)):
            return json.load(open(_x(p))), _x(p)
    return {}, None


def resolve(args, need_bundle=True):
    """Merge config file + environment + CLI into one settings dict.

    Keys: id, name, bundle, audio_dir, mastered, stems, project_id, preset, preset_name,
    mcp_dir, builtin_dir, edit_skill_dir, target_lufs, target_tp, config_path."""
    cfg, cpath = load_config(getattr(args, "config", None))
    if "audio" in cfg and isinstance(cfg["audio"], dict):  # allow an "audio" section inside a bigger config
        cfg = dict(cfg, **cfg["audio"])
    projects = cfg.get("projects") or {}
    alias = getattr(args, "project", None) or cfg.get("default_project")
    if not alias and len(projects) == 1:
        alias = next(iter(projects))
    entry = {}
    if alias:
        if alias not in projects and not getattr(args, "bundle", None):
            raise SystemExit(f"unknown project {alias!r}; known: {sorted(projects)} (config: {cpath})")
        entry = dict(projects.get(alias) or {})
    bundle = _x(getattr(args, "bundle", None) or entry.get("bundle"))
    if need_bundle and not bundle:
        raise SystemExit("no Borumi bundle: pass --bundle '<path>.bmprojbundle' or add a project to the config "
                         "(see config.example.json)")
    name = entry.get("name") or (os.path.splitext(os.path.basename(bundle.rstrip("/")))[0] if bundle else None)
    audio_dir = _x(getattr(args, "audio_dir", None) or entry.get("audio_dir"))
    if not audio_dir and bundle:
        audio_dir = os.path.join(os.path.dirname(bundle.rstrip("/")), f"{name} Agent", "audio")
    aup = dict(cfg.get("auphonic") or {})
    aup.update(entry.get("auphonic") or {})
    loud = dict(cfg.get("loudness") or {})
    loud.update(entry.get("loudness") or {})
    P = {
        "id": alias or (name or "project"),
        "name": name,
        "bundle": bundle,
        "audio_dir": audio_dir,
        "mastered": os.path.join(audio_dir, "mastered") if audio_dir else None,
        "stems": os.path.join(audio_dir, "stems") if audio_dir else None,
        "project_id": getattr(args, "project_id", None) or entry.get("project_id"),
        "preset": getattr(args, "preset", None) or aup.get("preset") or os.environ.get("AUPHONIC_PRESET"),
        "preset_name": getattr(args, "preset_name", None) or aup.get("preset_name") or "Auphonic preset",
        "mcp_dir": _x(os.environ.get("BORUMI_MCP_DIR") or cfg.get("borumi_mcp_dir") or DEFAULTS["borumi_mcp_dir"]),
        "builtin_dir": _x(os.environ.get("BORUMI_BUILTIN_SOUNDS") or cfg.get("borumi_builtin_sounds_dir")
                          or DEFAULTS["borumi_builtin_sounds_dir"]),
        "edit_skill_dir": _x(os.environ.get("BORUMI_EDIT_SKILL") or cfg.get("edit_skill_dir") or DEFAULTS["edit_skill_dir"]),
        "target_lufs": float(loud.get("target_lufs", DEFAULTS["target_lufs"])),
        "target_tp": float(loud.get("true_peak_dbtp", DEFAULTS["true_peak_dbtp"])),
        "config_path": cpath,
    }
    for k in ("preset", "project_id"):
        if P[k] and str(P[k]).startswith("<"):
            P[k] = None  # placeholder left in the example config
    return P


def require_preset(P):
    if not P.get("preset"):
        raise SystemExit("no Auphonic preset: set auphonic.preset in the config, pass --preset UUID or export "
                         "AUPHONIC_PRESET (the UUID of your preset, e.g. the one you named 'Broadcast Voice')")
    return P["preset"]


if __name__ == "__main__":
    ap = add_args(argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter))
    a = ap.parse_args()
    P = resolve(a, need_bundle=False)
    json.dump(P, sys.stdout, indent=1)
    print()
