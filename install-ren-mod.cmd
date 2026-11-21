@echo off
setlocal
if not defined REN_MODS_DIR set "REN_MODS_DIR=%RIN_MODS_DIR%"
if not defined REN_MODS_DIR set "REN_MODS_DIR=%USERPROFILE%\Zomboid\mods"
set "target=%REN_MODS_DIR%\RinDialogue"
if exist "%target%" if /i not "%~1"=="/update" (
    echo Ren Dialogue is already present at "%target%". Run with /update to refresh it.
    exit /b 1
)
set "client=%target%\common\media\lua\client"
if exist "%client%\RinDialogue.lua" (
    if exist "%client%\RenDialogue.lua" (
        ren "%client%\RinDialogue.lua" "RinDialogue.lua.previous"
    ) else (
        ren "%client%\RinDialogue.lua" "RenDialogue.lua"
    )
    if errorlevel 1 exit /b 1
)
xcopy "%~dp0mod\RenDialogue-Workshop\Contents\mods\RinDialogue" "%target%" /E /I /Q /Y
if errorlevel 1 exit /b 1
echo Installed Ren Dialogue at "%target%".
