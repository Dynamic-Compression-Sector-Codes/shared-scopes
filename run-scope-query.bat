@echo off
setlocal enabledelayedexpansion

:: Set script name, environment name, and configuration files
set "ENV_NAME=scope_control"
set "YAML_FILE=environment.yml"
set "SCRIPT_NAME=scope_query_tool.py"
set "MARKER_FILE=.conda_env_installed.yml"
set "SHORTCUT_PATH=%USERPROFILE%\Desktop\VOBB Timing Tool.lnk"
set "LOCAL_ICON_DIR=C:\ProgramData\ScopeControl\Icons"
set "LOCAL_ICON=%LOCAL_ICON_DIR%\scope_icon.ico"

echo ===================================================
echo          VOBB Timing Tool Launcher
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

    :: Fast check: detect if environment.yml changed since the last setup
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

:: 5. Activate env and ensure tool-specific packages are installed
echo Activating Conda environment '%ENV_NAME%'...
call "%CONDA_PATH%" activate %ENV_NAME%

:: xlwings and pywin32 are required by scope_query_tool.py but may not be in
:: the base environment.yml. Install silently if missing.
python -c "import xlwings" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo Installing xlwings...
    pip install xlwings --quiet
)
python -c "import win32com.client" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo Installing pywin32...
    pip install pywin32 --quiet
)

:: 6. Create a desktop shortcut if it doesn't exist
if not exist "%SHORTCUT_PATH%" (
    echo Creating a desktop shortcut for easy launching...

    :: Copy icon to a stable local path
    if not exist "%LOCAL_ICON_DIR%" mkdir "%LOCAL_ICON_DIR%"
    if exist "%~dp0ui\scope_icon.ico" copy /Y "%~dp0ui\scope_icon.ico" "%LOCAL_ICON%" >nul

    if exist "%LOCAL_ICON%" (
        powershell.exe -ExecutionPolicy Bypass -Command "& { $ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT_PATH%'); $s.TargetPath = '%~f0'; $s.WorkingDirectory = '%~dp0'; $s.IconLocation = '%LOCAL_ICON%'; $s.Save() }"
    ) else (
        powershell.exe -ExecutionPolicy Bypass -Command "& { $ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT_PATH%'); $s.TargetPath = '%~f0'; $s.WorkingDirectory = '%~dp0'; $s.Save() }"
    )
    if !ERRORLEVEL! equ 0 (
        echo Shortcut 'VOBB Timing Tool.lnk' created on Desktop.
    )
    echo.
)

:: 7. Launch the script
echo Launching %SCRIPT_NAME%...
python "%SCRIPT_NAME%"

echo.
echo Application closed.
echo.
REM pause
