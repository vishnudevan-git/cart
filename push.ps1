# push.ps1 - Upload latest changes to GitHub
# Usage:  right-click -> Run with PowerShell
#    or:  .\push.ps1 "your commit message"

param(
    [string]$Message = ""
)

$ErrorActionPreference = "Stop"

# Move to the folder this script lives in
Set-Location -Path $PSScriptRoot

Write-Host "=== Changes to be uploaded ===" -ForegroundColor Cyan
git status --short

# Stage everything: new + modified + deleted files
git add -A

# Nothing changed? stop early.
if (-not (git status --porcelain)) {
    Write-Host "Nothing to upload. Everything is already on GitHub." -ForegroundColor Green
    exit 0
}

# Safety check: block common secret/data files from going up
$blocked = git diff --cached --name-only | Select-String -Pattern '(^|/)\.env$|\.pem$|\.key$|db\.sqlite3|\.bak-'
if ($blocked) {
    Write-Host "STOPPED: these look like secret/data files and should not be pushed:" -ForegroundColor Red
    $blocked | ForEach-Object { Write-Host "   $_" -ForegroundColor Red }
    Write-Host "Add them to .gitignore, then run again." -ForegroundColor Yellow
    exit 1
}

# Build a commit message
if ([string]::IsNullOrWhiteSpace($Message)) {
    $Message = "Update " + (Get-Date -Format "yyyy-MM-dd HH:mm")
}

git commit -m "$Message"
git push

Write-Host ""
Write-Host "Done. Changes are live at https://github.com/vishnudevan-git/cart" -ForegroundColor Green
