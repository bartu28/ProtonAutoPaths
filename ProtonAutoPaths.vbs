Dim oShell, oFSO, scriptDir, pythonw
Set oShell = CreateObject("WScript.Shell")
Set oFSO   = CreateObject("Scripting.FileSystemObject")

scriptDir = oFSO.GetParentFolderName(WScript.ScriptFullName)
pythonw   = oShell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python312\pythonw.exe"

If Not oFSO.FileExists(pythonw) Then
    ' Fall back to pythonw on PATH
    pythonw = "pythonw"
End If

oShell.Run """" & pythonw & """ """ & scriptDir & "\main.py"" --ui", 0, False
