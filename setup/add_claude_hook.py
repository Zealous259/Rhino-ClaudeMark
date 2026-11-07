"""Add ClaudeMark's prompt hook to Claude Code's user settings (~/.claude/settings.json).

    python setup/add_claude_hook.py            add it (or point it at this folder again after a move)
    python setup/add_claude_hook.py --check    show what would change, write nothing
    python setup/add_claude_hook.py --python "C:/Python312/python.exe"    run the hook with that interpreter
    python setup/add_claude_hook.py --settings <file>                     edit another settings file

The hook is a UserPromptSubmit command: each time you send a prompt in any Claude Code session, it attaches the
captures ClaudeMark has waiting. The file is backed up to settings.json.bak first, every other setting is kept,
and running it twice does not add it twice. Restart Claude Code afterwards (or open /hooks) so it is loaded.
"""
import argparse
import json
import os
import shutil
import sys

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(KIT, "hook", "rhino_markup_inbox.py").replace("\\", "/")
MARK = "rhino_markup_inbox.py"

ap = argparse.ArgumentParser(description="Add ClaudeMark's UserPromptSubmit hook to Claude Code.")
ap.add_argument("--settings", default=os.path.join(os.path.expanduser("~"), ".claude", "settings.json"))
ap.add_argument("--python", default="python", help="the interpreter Claude Code should run the hook with")
ap.add_argument("--check", action="store_true", help="show the change, write nothing")
a = ap.parse_args()

if not os.path.isfile(HOOK):
    sys.exit("The hook is missing: %s" % HOOK)
cfg = {}
if os.path.isfile(a.settings):
    raw = open(a.settings, encoding="utf-8").read()
    try:
        cfg = json.loads(raw) if raw.strip() else {}
    except ValueError as e:
        sys.exit("%s is not valid JSON (%s). Fix it first; nothing was changed." % (a.settings, e))
command = '%s "%s"' % (a.python if " " not in a.python else '"%s"' % a.python, HOOK)
entry = {"type": "command", "command": command, "timeout": 10}

groups = cfg.setdefault("hooks", {}).setdefault("UserPromptSubmit", [])
found = False
for g in groups:
    for h in g.get("hooks", []):
        if MARK in h.get("command", ""):
            found = True
            if h.get("command") == command:
                print("Already set: %s" % command)
                sys.exit(0)
            print("Updating:\n  was  %s\n  now  %s" % (h.get("command"), command))
            h["command"] = command
            h.setdefault("timeout", 10)
if not found:
    groups.append({"hooks": [entry]})
    print("Adding the UserPromptSubmit hook:\n  %s" % command)

if a.check:
    print("--check: %s was not changed." % a.settings)
    sys.exit(0)
os.makedirs(os.path.dirname(os.path.abspath(a.settings)), exist_ok=True)
if os.path.isfile(a.settings):
    shutil.copy2(a.settings, a.settings + ".bak")
    print("Backup: %s.bak" % a.settings)
with open(a.settings, "w", encoding="utf-8", newline="\n") as fh:
    json.dump(cfg, fh, indent=2)
    fh.write("\n")
print("Saved %s. Restart Claude Code so the hook is loaded." % a.settings)
