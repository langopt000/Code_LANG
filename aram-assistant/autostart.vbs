Option Explicit
' Hidden launcher for the ARAM Mayhem assistant watcher.
' Installed into the user's Startup folder by setup_autostart.py.
' The project dir and python path below are filled in at install time,
' so this file keeps working after the project is moved to another folder.
Dim sh, fso, folder, py, cmd
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

folder = "__ROOT__"
py = "__PY__"

If py = "" Or InStr(py, "__") > 0 Then
    py = "pythonw.exe"
End If
If Not fso.FileExists(py) Then
    py = "pythonw.exe"
End If

If py = "pythonw.exe" Then
    cmd = "pythonw.exe """ & folder & "\watcher.py"""
Else
    cmd = """" & py & """ """ & folder & "\watcher.py"""
End If

sh.CurrentDirectory = folder
sh.Run cmd, 0, False
