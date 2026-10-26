' Starts Reverse Engineer with no console window (Windows 10/11).
' First run (no .venv yet): opens a visible console that builds the venv, then starts the app.
' Without this file, the same works from a terminal in this folder: py -3 launch.py --setup
' If Windows blocks this file after a ZIP download: right-click it, Properties, tick Unblock.
Option Explicit
Dim fso, sh, here, pyw, failed
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = here
pyw = here & "\.venv\Scripts\pythonw.exe"
If fso.FileExists(pyw) Then
  sh.Run """" & pyw & """ """ & here & "\launch.py""", 0, False
Else
  On Error Resume Next
  sh.Run "py.exe -3 """ & here & "\launch.py"" --setup", 1, False
  failed = Err.Number <> 0
  On Error GoTo 0
  If failed Then
    MsgBox "Python 3.11 or newer is needed, with its py launcher. Install it from python.org, " & _
           "then open this file again.", vbExclamation, "Reverse Engineer"
  End If
End If
