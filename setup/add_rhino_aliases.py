#! python 2
# -*- coding: utf-8 -*-
"""Add the four ClaudeMark aliases to Rhino, pointing at the folder this script is in.

Run it once from Rhino's command line (paste the line, with your own path):
    _-RunPythonScript "C:\\path\\to\\Rhino-ClaudeMark\\setup\\add_rhino_aliases.py"

It adds ClaudeMark, ClaudeReply, ClaudeMarkClassic and ClaudeReplyClassic, or updates them if they exist (run it again
after moving the folder). The aliases are saved with Rhino's settings when Rhino closes; they work straight away.
"""
import os

import Rhino

f = globals().get("__file__")
if not f or not os.path.isfile(f):
    raise Exception("Run this file with _-RunPythonScript and its full path, so it can find its own folder.")
KIT = os.path.dirname(os.path.dirname(os.path.abspath(f)))
ALIASES = [("ClaudeMark", "claude_mark.py"), ("ClaudeReply", "claude_reply.py"),
           ("ClaudeMarkClassic", os.path.join("classic", "claude_mark.py")),
           ("ClaudeReplyClassic", os.path.join("classic", "claude_reply.py"))]

table = Rhino.ApplicationSettings.CommandAliasList
for name, rel in ALIASES:
    path = os.path.join(KIT, rel)
    if not os.path.isfile(path):
        print("ClaudeMark setup: %s is missing, alias %s skipped" % (path, name))
        continue
    macro = '! _-RunPythonScript "%s"' % path
    if table.IsAlias(name):
        table.SetMacro(name, macro)
        print("ClaudeMark setup: updated %s  ->  %s" % (name, macro))
    else:
        table.Add(name, macro)
        print("ClaudeMark setup: added %s  ->  %s" % (name, macro))
print("ClaudeMark setup: done. Type ClaudeMark to open the window.")
