@echo off
setlocal enabledelayedexpansion

:: Set script name, environment name, and configuration files
set "ENV_NAME=scope_control"
set "YAML_FILE=environment.yml"
set "SCRIPT_NAME=ScopeControl_V3.py"
set "MARKER_FILE=.conda_env_installed.yml"
set "SHORTCUT_PATH=%USERPROFILE%\Desktop\Scope Control v3.lnk"

echo ===================================================
echo             Scope Control Launcher
echo ===================================================
echo.

:: 1. Navigate to the repository root directory
cd /d "%~dp0"
echo Active directory: %CD%

:: 2. Pull the latest code updates from Git
echo Checking for code updates from GitHub...
git pull
if %ERRORLEVEL% neq 0 (
    echo WARNING: Git pull failed. You might be offline or have local conflicts.
    echo Launching the application using the current local files...
)
echo.

:: 3. Find the local Miniconda or Anaconda installation
echo Locating Conda installation...
set "CONDA_PATH="

:: Check common installation paths
if not defined CONDA_PATH if exist "%USERPROFILE%\miniconda3\condabin\conda.bat" set "CONDA_PATH=%USERPROFILE%\miniconda3\condabin\conda.bat"
if not defined CONDA_PATH if exist "%USERPROFILE%\AppData\Local\miniconda3\condabin\conda.bat" set "CONDA_PATH=%USERPROFILE%\AppData\Local\miniconda3\condabin\conda.bat"
if not defined CONDA_PATH if exist "%USERPROFILE%\anaconda3\condabin\conda.bat" set "CONDA_PATH=%USERPROFILE%\anaconda3\condabin\conda.bat"
if not defined CONDA_PATH if exist "%USERPROFILE%\AppData\Local\anaconda3\condabin\conda.bat" set "CONDA_PATH=%USERPROFILE%\AppData\Local\anaconda3\condabin\conda.bat"
if not defined CONDA_PATH if exist "C:\ProgramData\miniconda3\condabin\conda.bat" set "CONDA_PATH=C:\ProgramData\miniconda3\condabin\conda.bat"
if not defined CONDA_PATH if exist "C:\ProgramData\anaconda3\condabin\conda.bat" set "CONDA_PATH=C:\ProgramData\anaconda3\condabin\conda.bat"

:: Fallback: search system PATH for conda.bat
if not defined CONDA_PATH (
    for /f "delims=" %%i in ('where conda.bat 2^>nul') do (
        set "CONDA_PATH=%%i"
    )
)

if not defined CONDA_PATH (
    echo ERROR: Conda executable ^(conda.bat^) could not be found.
    echo Please make sure Miniconda or Anaconda is installed and added to your system PATH.
    echo.
    pause
    exit /b 1
)

echo Found Conda at: %CONDA_PATH%
echo.

:: 4. Verify and configure the Conda environment
echo Verifying Conda environment '%ENV_NAME%'...
call "%CONDA_PATH%" env list | findstr /C:"%ENV_NAME%" >nul
if %ERRORLEVEL% neq 0 (
    echo Environment '%ENV_NAME%' does not exist. Creating it now...
    call "%CONDA_PATH%" env create -n %ENV_NAME% -f "%YAML_FILE%"
    if %ERRORLEVEL% neq 0 (
        echo ERROR: Failed to create Conda environment.
        pause
        exit /b 1
    )
    copy /y "%YAML_FILE%" "%MARKER_FILE%" >nul
) else (
    echo Environment '%ENV_NAME%' already exists.
    
    :: Fast check: Check if environment.yml has changed since the last setup
    set "NEEDS_UPDATE=0"
    if not exist "%MARKER_FILE%" (
        set "NEEDS_UPDATE=1"
    ) else (
        fc /b "%YAML_FILE%" "%MARKER_FILE%" >nul 2>&1
        if !ERRORLEVEL! neq 0 set "NEEDS_UPDATE=1"
    )
    
    if "!NEEDS_UPDATE!"=="1" (
        echo Changes detected in '%YAML_FILE%'. Updating environment dependencies...
        call "%CONDA_PATH%" env update -n %ENV_NAME% -f "%YAML_FILE%" --prune
        if !ERRORLEVEL! equ 0 (
            copy /y "%YAML_FILE%" "%MARKER_FILE%" >nul
            echo Environment successfully updated.
        ) else (
            echo WARNING: Environment update encountered errors. Attempting to continue...
        )
    ) else (
        echo Environment dependencies are up to date.
    )
)
echo.

:: 5. Create a desktop shortcut if it doesn't exist
if not exist "%SHORTCUT_PATH%" (
    echo Creating a desktop shortcut for easy launching...
    set "ICON_PATH=%~dp0ui\ScopeICO.ico"
    if not exist "!ICON_PATH!" set "ICON_PATH="
    
    if defined ICON_PATH (
        powershell.exe -ExecutionPolicy Bypass -Command "& { $ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT_PATH%'); $s.TargetPath = '%~f0'; $s.WorkingDirectory = '%~dp0'; $s.IconLocation = '%~dp0ui\ScopeICO.ico'; $s.Save() }"
    ) else (
        powershell.exe -ExecutionPolicy Bypass -Command "& { $ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT_PATH%'); $s.TargetPath = '%~f0'; $s.WorkingDirectory = '%~dp0'; $s.Save() }"
    )
    if !ERRORLEVEL! equ 0 (
        echo Shortcut 'Scope Control v3.lnk' created on Desktop.
    )
    echo.
)

:: 6. Activate env and launch script
echo Activating Conda environment '%ENV_NAME%'...
call "%CONDA_PATH%" activate %ENV_NAME%

echo Launching %SCRIPT_NAME%...
python "%SCRIPT_NAME%"

echo.
echo Application closed.
echo.
REM pause
