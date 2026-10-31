' Launches task.cmd with its arguments in a hidden window, so scheduled runs
' don't flash a console every 30 minutes. Waits for it and passes its exit code back
' to Task Scheduler.
Set shell = CreateObject("WScript.Shell")
here = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
args = ""
For Each a In WScript.Arguments
    args = args & " " & a
Next
WScript.Quit shell.Run("""" & here & "\task.cmd""" & args, 0, True)
