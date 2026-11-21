Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = scriptDir
Set pythonExe = Nothing
Dim localPython
localPython = WshShell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Python\pythoncore-3.14-64\pythonw.exe")
If fso.FileExists(localPython) Then
    WshShell.Run """" & localPython & """ """ & scriptDir & "\app.py""", 0, False
Else
    WshShell.Run "pythonw.exe """ & scriptDir & "\app.py""", 0, False
End If
