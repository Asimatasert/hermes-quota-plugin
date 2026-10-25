' Hermes Agent Bar - starts AgentBar.ps1 with no console window.
' Lives next to AgentBar.ps1; the scheduled task runs: wscript.exe "<dir>\start-hidden.vbs"
Option Explicit
Dim shell, fso, dir, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
cmd = "powershell.exe -NoProfile -NonInteractive -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & dir & "\AgentBar.ps1"""
shell.Run cmd, 0, False
