#! python 2
# -*- coding: utf-8 -*-
"""ClaudeReplyClassic: the original ClaudeReply, in the classic window (kept as a fallback for ClaudeMark 4).

Run from the ClaudeReplyClassic alias:
    ! _-RunPythonScript "<kit folder>\\classic\\claude_reply.py"
"""
import os
import System

CLAUDEMARK_MODE = "reply"
_here = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else ""
_path = os.path.join(_here, "claude_mark.py")
if not os.path.isfile(_path):
    _path = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.DesktopDirectory),
                         "Rhino-ClaudeMark", "classic", "claude_mark.py")
exec(compile(open(_path).read(), _path, "exec"))
