' Last.fm Tracker - Background Startup Script
' This script starts the tracker without showing a console window
' Useful for startup folder or scheduled tasks

Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
WshShell.Run "cmd /c venv\Scripts\pythonw.exe run_service.py", 0, False
