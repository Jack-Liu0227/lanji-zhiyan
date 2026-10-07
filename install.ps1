<#
  科研工作 1.0 安装器（Windows）
  ------------------------------------------------------------------
  给别人用（从 GitHub Releases 拉最新版，装到 %LOCALAPPDATA%、建桌面快捷方式、直接启动）：
      irm https://raw.githubusercontent.com/3276166289-code/research-workbench/main/install.ps1 | iex
  开发时（装本地刚打出来的包）：
      .\install.ps1 -Source .\dist\ResearchWorkbench
  说明：重装不会动 data/（库、PDF、笔记都在那），用户数据不会被覆盖。
#>
param(
    [string]$Repo = "3276166289-code/research-workbench",
    [string]$InstallRoot = "$env:LOCALAPPDATA\Programs\ResearchWorkbench",
    [string]$Source = ""
)
$ErrorActionPreference = "Stop"

if (-not $Source) {
    $url = "https://github.com/$Repo/releases/latest/download/ResearchWorkbench.zip"
    $zip = Join-Path $env:TEMP "ResearchWorkbench.zip"
    $tmp = Join-Path $env:TEMP "rw-unpack"
    Write-Host "下载最新版：$url"
    Invoke-WebRequest $url -OutFile $zip -UseBasicParsing
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive $zip -DestinationPath $tmp -Force
    $inner = Get-ChildItem $tmp -Directory |
        Where-Object { Test-Path (Join-Path $_.FullName "ResearchWorkbench.exe") } | Select-Object -First 1
    $Source = if ($inner) { $inner.FullName } else { $tmp }
}
if (-not (Test-Path (Join-Path $Source "ResearchWorkbench.exe"))) { throw "包里没有 ResearchWorkbench.exe：$Source" }

Get-Process ResearchWorkbench -ErrorAction SilentlyContinue | Stop-Process -Force   # 覆盖安装前先关掉在跑的旧进程

Write-Progress -Activity "科研工作 1.0 安装" -Status "复制到 $InstallRoot" -PercentComplete 20
New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
$files = Get-ChildItem -Path $Source -Recurse -File
for ($i = 0; $i -lt $files.Count; $i++) {
    $relative = $files[$i].FullName.Substring($Source.Length).TrimStart('\')
    $target = Join-Path $InstallRoot $relative
    New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
    Copy-Item $files[$i].FullName $target -Force
    Write-Progress -Activity "科研工作 1.0 安装" -Status $relative -PercentComplete (20 + [int](70 * ($i + 1) / $files.Count))
}

Write-Progress -Activity "科研工作 1.0 安装" -Status "创建桌面快捷方式" -PercentComplete 95
$desktop = [Environment]::GetFolderPath('Desktop')
$lnk = Join-Path $desktop "科研工作 1.0.lnk"
$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($lnk)
$sc.TargetPath = Join-Path $InstallRoot "ResearchWorkbench.exe"
$sc.WorkingDirectory = $InstallRoot
$sc.IconLocation = Join-Path $InstallRoot "build\icon.ico"
$sc.Description = "科研工作 1.0"
$sc.Save()
Write-Progress -Activity "科研工作 1.0 安装" -Completed

Write-Host ""
Write-Host "装好了：      $InstallRoot"
Write-Host "快捷方式：    $lnk"
Write-Host "第一次打开：到「模型」页填自己的 API key（不填不能用模型，检索/精读也走模型）。"
Start-Process -FilePath (Join-Path $InstallRoot "ResearchWorkbench.exe") -WorkingDirectory $InstallRoot
