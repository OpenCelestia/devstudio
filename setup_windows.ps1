# Windows 唯一入口。
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1 --install-only
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1 --product anypi_nova
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$shimDir = Join-Path $env:LOCALAPPDATA "Anyka\DevStudio"
$venvPython = Join-Path $shimDir ".venv\Scripts\python.exe"
$shim = Join-Path $shimDir "devstudio.cmd"



function Get-WslLinuxPath([string]$winPath) {
    # Z:\home\lian\foo -> /home/lian/foo
    if ($winPath -match '^[A-Za-z]:\\home\\(.+)$') {
        return '/home/' + ($Matches[1] -replace '\\', '/')
    }
    if ($winPath -match '^\\\\wsl(?:\.localhost|\$)\\[^\\]+\\home\\(.+)$') {
        return '/home/' + ($Matches[1] -replace '\\', '/')
    }
    return $null
}

function Resolve-DevStudioRoot([string]$root) {
    # Prefer a Windows drive-letter path. Path.resolve() via WSL/Python can yield
    # \\wsl.localhost\... which hangs Win32 Python over 9P.
    $full = [System.IO.Path]::GetFullPath($root)
    if ($full -match '^\\\\wsl(\.localhost|\$)\\') {
        if ($root -match '^[A-Za-z]:\\') {
            return [System.IO.Path]::GetFullPath($root)
        }
        # Fall back: map from known Z: / network mounts if caller passed relative.
        try {
            $rp = (Resolve-Path -LiteralPath $root).Path
            if ($rp -match '^[A-Za-z]:\\') { return $rp }
        } catch { }
    }
    if ($full -match '^[A-Za-z]:\\') { return $full }
    try {
        $rp = (Resolve-Path -LiteralPath $root).Path
        if ($rp -match '^[A-Za-z]:\\') { return $rp }
    } catch { }
    return $full
}

function Find-Python {
    $names = @("python.exe", "python3.exe", "py.exe")
    $dirs = $env:PATH.Split(';') | Where-Object { $_ -ne "" }
    foreach ($dir in $dirs) {
        foreach ($name in $names) {
            $source = Join-Path $dir $name
            if (-not (Test-Path -LiteralPath $source)) { continue }
            if ((Get-Item -LiteralPath $source).Length -eq 0) { continue }
            $args = @()
            if ($name -eq "py.exe") { $args = @("-3") }
            & $source @args -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
            if ($LASTEXITCODE -eq 0) {
                return @{ File = $source; Args = $args }
            }
        }
    }
    return $null
}

function ConvertTo-GitBashPath([string]$winPath) {
    $p = $winPath -replace '\\', '/'
    if ($p -match '^([A-Za-z]):/(.*)$') {
        return '/' + $Matches[1].ToLower() + '/' + $Matches[2]
    }
    return $p
}



function Sync-RuntimeTree([string]$actualRoot) {
    # Keep a local NTFS mirror so Win32 Python never imports / reads adapters over Z:/9P.
    $name = "devstudio"
    $dst = Join-Path $shimDir "runtime-tree"
    $pairs = @(
        @{ Src = Join-Path $actualRoot ("src\" + $name); Dst = Join-Path $dst ("src\" + $name) },
        @{ Src = Join-Path $actualRoot "sdk"; Dst = Join-Path $dst "sdk" },
        @{ Src = Join-Path $actualRoot "celestia"; Dst = Join-Path $dst "celestia" }
    )
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    Copy-Item -LiteralPath (Join-Path $actualRoot "pyproject.toml") -Destination (Join-Path $dst "pyproject.toml") -Force -ErrorAction SilentlyContinue
    foreach ($pair in $pairs) {
        if (-not (Test-Path -LiteralPath $pair.Src)) { continue }
        New-Item -ItemType Directory -Force -Path $pair.Dst | Out-Null
        & robocopy $pair.Src $pair.Dst /MIR /NFL /NDL /NJH /NJS /nc /ns /np /XD __pycache__ .git .venv node_modules 2>&1 | Out-Null
        if ($LASTEXITCODE -ge 8) {
            Write-Host "同步失败：$($pair.Src) -> $($pair.Dst) (robocopy=$LASTEXITCODE)"
            exit $LASTEXITCODE
        }
    }
    return $dst
}


function Write-Shim([string]$actualRoot) {
    $cmdName = "devstudio"
    $tree = Sync-RuntimeTree $actualRoot
    $pySrc = Join-Path $tree "src"
    $bashTree = ConvertTo-GitBashPath $tree
    $bashPy = ConvertTo-GitBashPath $venvPython
    $bashShim = Join-Path $shimDir $cmdName
    $linuxRoot = Get-WslLinuxPath $actualRoot
    if (-not $linuxRoot) { $linuxRoot = "" }

    @"
@echo off
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
chcp 65001 >nul
set "DEVSTUDIO_ROOT=$tree"
set "DEVSTUDIO_BIN=%~f0"
set "PYTHONPATH=$pySrc;%PYTHONPATH%"
"$venvPython" -m $cmdName %*
"@ | Set-Content -LiteralPath $shim -Encoding ascii

    # Extensionless shim: Git Bash uses Win32 python; real WSL/Linux must NOT
    # pick this file over ~/.local/bin — if it does, detect Linux and re-exec.
    $bashBody = @"
#!/bin/sh
# Anyka DevStudio launcher (Windows Git Bash + accidental WSL PATH hit)
if [ "`$(uname -s 2>/dev/null)" = "Linux" ]; then
  LINUX_ROOT="$linuxRoot"
  if [ -z "`$LINUX_ROOT" ]; then
    LINUX_ROOT="`$HOME/celestia/develop/devstudio"
  fi
  if [ -x "`$HOME/.local/bin/devstudio" ] && [ "`$(readlink -f "`$0" 2>/dev/null || echo "`$0")" != "`$(readlink -f "`$HOME/.local/bin/devstudio" 2>/dev/null || true)" ]; then
    # Prefer the real Linux launcher when present.
    case ": `$PATH:" in
      *": `$HOME/.local/bin:"*) ;;
      *) PATH="`$HOME/.local/bin:`$PATH" ;;
    esac
    if [ -x "`$HOME/.local/bin/devstudio" ]; then
      exec "`$HOME/.local/bin/devstudio" "`$@"
    fi
  fi
  export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
  export DEVSTUDIO_ROOT="`$LINUX_ROOT"
  export PYTHONPATH="`$LINUX_ROOT/src`${PYTHONPATH:+:`$PYTHONPATH}"
  if [ -x "`$LINUX_ROOT/.venv/bin/python" ]; then
    exec "`$LINUX_ROOT/.venv/bin/python" -m $cmdName "`$@"
  fi
  exec python3 -m $cmdName "`$@"
fi
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export DEVSTUDIO_ROOT="$bashTree"
export DEVSTUDIO_BIN="$bashTree/$cmdName"
export PYTHONPATH="$bashTree/src`${PYTHONPATH:+:`$PYTHONPATH}"
exec "$bashPy" -m $cmdName "`$@"
"@
    $bashBody = $bashBody -replace "`r`n", "`n" -replace "`r", "`n"
    if (-not $bashBody.EndsWith("`n")) { $bashBody = $bashBody + "`n" }
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($bashShim, $bashBody, $utf8)
    Write-Host "启动器模式：Win32 venv + runtime-tree（$tree）"
}

function Disable-BrokenLaunchers {
    # Neutralize pip/console-script leftovers that raise ModuleNotFoundError.
    # Git Bash prefers .exe over later .cmd; a broken Scripts\devstudio.exe steals the name.
    $cmdName = "devstudio"
    $seen = @{}
    $dirs = @()
    foreach ($scope in @("Process", "User", "Machine")) {
        $value = [Environment]::GetEnvironmentVariable("Path", $scope)
        if ([string]::IsNullOrEmpty($value)) { continue }
        foreach ($part in ($value -split ';')) {
            $trim = $part.Trim()
            if ($trim -eq "") { continue }
            $key = $trim.TrimEnd('\').ToLowerInvariant()
            if ($seen.ContainsKey($key)) { continue }
            $seen[$key] = $true
            $dirs += $trim
        }
    }
    foreach ($dir in $dirs) {
        if ($dir.TrimEnd('\').ToLowerInvariant() -eq $shimDir.TrimEnd('\').ToLowerInvariant()) {
            continue
        }
        foreach ($name in @("$cmdName.exe", "$cmdName.bat", "$cmdName.cmd")) {
            $candidate = Join-Path $dir $name
            if (-not (Test-Path -LiteralPath $candidate)) { continue }
            $probe = $null
            try {
                $probe = & $candidate --version 2>&1 | Out-String
            } catch {
                $probe = $_.Exception.Message
            }
            $joined = "$probe"
            $broken = ($LASTEXITCODE -ne 0) -or ($joined -match "ModuleNotFoundError|No module named")
            if (-not $broken) {
                Write-Host "保留可用的第三方入口：$candidate"
                continue
            }
            $bak = "$candidate.broken-by-devstudio-setup"
            if (Test-Path -LiteralPath $bak) {
                Remove-Item -LiteralPath $candidate -Force -ErrorAction SilentlyContinue
            } else {
                Rename-Item -LiteralPath $candidate -NewName (Split-Path $bak -Leaf) -Force
            }
            Write-Host "已停用损坏的 CLI 入口：$candidate"
            Write-Host "  （备份为 $bak；请用 setup 安装的启动器，勿对第三方 Python pip install anyka-devstudio）"
        }
    }
}

function Install-Runtime {
    New-Item -ItemType Directory -Force -Path $shimDir | Out-Null
    if (-not (Test-Path -LiteralPath $venvPython)) {
        $python = Find-Python
        if (-not $python) {
            Write-Host "未找到可用的 Python 3.11 或更高版本。"
            Write-Host "请先安装 Python，并勾选 Add python.exe to PATH。然后新开终端再执行本脚本。"
            exit 1
        }
        & $python.File @($python.Args) -m venv (Join-Path $shimDir ".venv")
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }

    $actualRoot = Resolve-DevStudioRoot $Root
    Write-Shim $actualRoot
    Disable-BrokenLaunchers

    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    if ([string]::IsNullOrEmpty($user)) { $user = "" }
    if ($user.Contains(';')) {
        $rawParts = $user.Split(';')
    } else {
        $rawParts = [regex]::Split($user, '(?=[A-Za-z]:\\)')
    }
    $parts = @($rawParts | ForEach-Object { $_.Trim() } | Where-Object {
        $_ -ne "" -and $_.TrimEnd('\') -ine $shimDir.TrimEnd('\')
    })
    $newUserPath = (@($shimDir) + $parts) -join ';'
    [Environment]::SetEnvironmentVariable("Path", $newUserPath, "User")

    & $shim --version
    if ($LASTEXITCODE -ne 0) {
        Write-Host "安装验证失败：$shim --version"
        exit $LASTEXITCODE
    }
    Write-Host "已安装 devstudio。启动器：$shim"
    Write-Host "请保留这个 DevStudio 目录，Adapter 和 Skill 从这里读取。"
}

function Test-ChipProject([string]$dir) {
    if (-not (Test-Path -LiteralPath $dir)) { return $false }
    $celestia = @("application", "framework", "hardware") | Where-Object { -not (Test-Path -LiteralPath (Join-Path $dir $_)) }
    if ($celestia.Count -eq 0 -and (Test-Path -LiteralPath (Join-Path $dir "build.sh"))) { return $true }
    $sdkNames = @("os", "platform", "tools") | Where-Object { -not (Test-Path -LiteralPath (Join-Path $dir $_)) }
    if ($sdkNames.Count -eq 0 -and ((Split-Path $dir -Leaf) -match "^V\d+")) { return $true }
    $system = Join-Path $dir "system"
    if (Test-Path -LiteralPath $system) {
        $found = Get-ChildItem -LiteralPath $system -Directory -ErrorAction SilentlyContinue | ForEach-Object {
            Get-ChildItem -LiteralPath $_.FullName -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -match "^V\d+" }
        }
        if ($found) { return $true }
    }
    return $false
}

function Find-Project([string]$Root) {
    $parent = Split-Path $Root -Parent
    if (Test-ChipProject $parent) { return $parent }
    $hits = @()
    Get-ChildItem -LiteralPath $parent -Directory -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.FullName -ne $Root -and (Test-ChipProject $_.FullName)) { $hits += $_.FullName }
    }
    if ($hits.Count -eq 1) { return $hits[0] }
    return ,$hits
}

function Show-Usage {
    Write-Host "用法："
    Write-Host "  powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1"
    Write-Host "  powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1 <工程目录>"
    Write-Host "  powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1 [客户工程] --sdk <目录>"
    Write-Host "  可追加 --product <产品名>"
    Write-Host "  powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1 --install-only"
}

$installOnly = $false
$projectArg = $null
$sdkArg = $null
$productArg = $null
for ($i = 0; $i -lt $args.Count; $i++) {
    $item = $args[$i]
    if ($item -eq "--install-only") {
        $installOnly = $true
    } elseif ($item -eq "--sdk") {
        if ($i + 1 -ge $args.Count) { Show-Usage; exit 2 }
        $i++
        $sdkArg = $args[$i]
    } elseif ($item -eq "--product") {
        if ($i + 1 -ge $args.Count) { Show-Usage; exit 2 }
        $i++
        $productArg = $args[$i]
    } elseif ($item.StartsWith("-")) {
        Show-Usage
        exit 2
    } elseif ($null -eq $projectArg) {
        $projectArg = $item
    } else {
        Show-Usage
        exit 2
    }
}

if (-not (Test-Path -LiteralPath $venvPython) -or -not (Test-Path -LiteralPath $shim)) {
    Install-Runtime
} else {
    $actualRoot = Resolve-DevStudioRoot $Root
    Write-Shim $actualRoot
    Disable-BrokenLaunchers
}

if ($installOnly) {
    Write-Host "请新开一个终端，执行 devstudio --version（cmd / Git Bash / PowerShell 均可）。已经打开的 IDE 请重启。"
    Write-Host "然后进入工程目录再执行 setup_windows.ps1，或直接执行 devstudio init。"
    exit 0
}

$ActualRoot = Resolve-DevStudioRoot $Root
$env:DEVSTUDIO_ROOT = $ActualRoot
$sourceRoot = Sync-RuntimeTree $ActualRoot
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$sourceRoot;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = $sourceRoot
}

if (-not $projectArg -and -not $sdkArg) {
    $auto = Find-Project $Root
    $noAuto = $null -eq $auto -or ($auto -is [System.Array] -and $auto.Count -eq 0)
    if ($noAuto) {
        Write-Host "未自动识别工程，请选择源码组合："
        Write-Host "  1) Celestia + SDK"
        Write-Host "  2) 只有芯片 SDK"
        Write-Host "  3) 客户应用代码 + SDK"
        $choice = Read-Host "请选择 [1/2/3]"
        if ($choice -eq "1") {
            $projectArg = Read-Host "Celestia 工程根目录"
        } elseif ($choice -eq "2") {
            $projectArg = Read-Host "具体 SDK 版本目录（例如 AnyCloud39AV200\V1.04）"
        } elseif ($choice -eq "3") {
            $defaultProject = Split-Path $Root -Parent
            $enteredProject = Read-Host "客户项目根目录 [$defaultProject]"
            $projectArg = if ($enteredProject) { $enteredProject } else { $defaultProject }
            $sdkArg = Read-Host "SDK 版本子目录（例如 sdk\AnyCloud39AV100_V2\V2.05）"
            if (-not $sdkArg) {
                Write-Host "客户应用场景必须指定 SDK 版本目录。"
                exit 2
            }
        } else {
            Write-Host "未选择有效的源码组合。"
            exit 2
        }
    }
}

if ($sdkArg) {
    if ($projectArg) {
        $project = (Resolve-Path -LiteralPath $projectArg).Path
    } else {
        $project = Split-Path $Root -Parent
    }
    if ([System.IO.Path]::IsPathRooted($sdkArg)) {
        $sdkCandidate = $sdkArg
    } else {
        $sdkCandidate = Join-Path $project $sdkArg
    }
    $sdk = (Resolve-Path -LiteralPath $sdkCandidate).Path
    $sdkNames = @("os", "platform", "tools") | Where-Object { -not (Test-Path -LiteralPath (Join-Path $sdk $_)) }
    if ($sdkNames.Count -ne 0 -or ((Split-Path $sdk -Leaf) -notmatch "^V\d+")) {
        Write-Host "指定目录不是具体的 SDK 版本目录：$sdk"
        Write-Host "目录必须是 <平台族>\<版本>，并包含 os、platform、tools。"
        exit 1
    }
} elseif ($projectArg) {
    $project = (Resolve-Path -LiteralPath $projectArg).Path
    if (-not (Test-ChipProject $project)) {
        Write-Host "这个目录不是 Celestia 工程，也不是芯片 SDK：$project"
        Show-Usage
        exit 1
    }
} else {
    $found = Find-Project $Root
    if ($null -eq $found -or ($found -is [System.Array] -and $found.Count -eq 0)) {
        Write-Host "没有找到 Celestia 工程或芯片 SDK。"
        Write-Host "客户应用工程必须用 --sdk 指定 SDK 子目录。"
        Show-Usage
        exit 1
    }
    if ($found -is [System.Array]) {
        Write-Host "找到多个工程或 SDK 版本，请明确指定工程目录后重跑。"
        exit 1
    }
    $project = $found
}

$env:DEVSTUDIO_BIN = $shim
Push-Location $project
$initArgs = @("-m", "devstudio", "init")
if ($sdkArg) {
    $initArgs += @("--state", "sdk_customer_app", "--sdk", $sdk)
}
if ($productArg) {
    $initArgs += @("--product", $productArg)
}
& $venvPython @initArgs
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { exit $code }
if (-not (Test-Path -LiteralPath (Join-Path $project "devstudio.yaml"))) {
    Write-Host "未初始化工程；没有写入 devstudio.yaml。"
    exit 0
}

Write-Host "已加载 $project。"
Write-Host "请在 VS Code/Cursor 的 Agent 面板中提出研发需求。"
Write-Host "DevStudio 使用 IDE 自带模型，不读取或保存模型 API Key。"
