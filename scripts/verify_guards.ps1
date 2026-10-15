# Replicates scripts/verify_mutation_catch.sh + verify_frontend_guards.sh
# (no bash on this box). Same logic: backup -> mutate -> target tests MUST go
# red -> restore -> confirm green.
# NOTE: no $ErrorActionPreference='Stop' — pytest's tmp cleanup can emit
# stderr noise (sandbox safe-delete notices) that must not abort this script.
Set-Location D:\Ai\DBA\MutiAgent\DogBuddyCn
$PY = '.\.venv\Scripts\python.exe'
$bak = Join-Path $env:TEMP 'dbcn-guard-bak'
New-Item -ItemType Directory -Force -Path $bak | Out-Null
$script:fail = 0

function Invoke-Mutate($file, $old, $new) {
    $s = [IO.File]::ReadAllText($file)
    if (-not $s.Contains($old)) { throw "mutation point not found in $file" }
    [IO.File]::WriteAllText($file, $s.Replace($old, $new))
}

function Test-Guard($name, $tests) {
    & $PY -m pytest $tests -q 2>&1 | Select-Object -Last 1 | Out-Host
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [X] ${name}: tests still GREEN after mutation - NOT CAUGHT"
        $script:fail = 1
    } else {
        Write-Host "  [OK] $name caught (pytest exit=$LASTEXITCODE)"
    }
}

Write-Host '###### v1: drop required_scope forwarding ######'
$reg = 'dogbuddy\tools\registry.py'
Copy-Item $reg (Join-Path $bak 'registry.py') -Force
try {
    Invoke-Mutate $reg 'required_scope=required)' 'x)'
    Test-Guard 'v1' @('tests/test_tool_whitelist.py', 'tests/test_security_boundary.py')
} finally {
    Copy-Item (Join-Path $bak 'registry.py') $reg -Force
}

Write-Host '###### v4: iteration path back to tool_names=None ######'
$ite = 'dogbuddy\services\iteration_executor.py'
Copy-Item $ite (Join-Path $bak 'ite.py') -Force
try {
    Invoke-Mutate $ite 'tool_names=role_spec.tool_names if role_spec else [],' 'tool_names=None,'
    Test-Guard 'v4' @('tests/test_tool_whitelist.py')
} finally {
    Copy-Item (Join-Path $bak 'ite.py') $ite -Force
}

Write-Host '###### frontend A: empty case body ######'
$app = 'dogbuddy\web\static\js\app.js'
Copy-Item $app (Join-Path $bak 'app.js') -Force
try {
    $s = [IO.File]::ReadAllText($app)
    $m = [regex]::Match($s, "(?s)(case 'projects':)(.*?)(\r?\n\s+case ')")
    if (-not $m.Success) { throw 'projects case block not found' }
    [IO.File]::WriteAllText($app, $s.Substring(0, $m.Groups[2].Index) + "`n          break;" + $s.Substring($m.Groups[2].Index + $m.Groups[2].Length))
    Test-Guard 'frontendA' @('tests/test_frontend_wiring.py')
} finally {
    Copy-Item (Join-Path $bak 'app.js') $app -Force
}

Write-Host '###### frontend B: renamed export ######'
$proj = 'dogbuddy\web\static\js\views-projects.js'
Copy-Item $proj (Join-Path $bak 'proj.js') -Force
try {
    Invoke-Mutate $proj 'DogBuddyViews.Projects =' 'DogBuddyViews.ProjectsX ='
    Test-Guard 'frontendB' @('tests/test_frontend_wiring.py')
} finally {
    Copy-Item (Join-Path $bak 'proj.js') $proj -Force
}

Write-Host '###### frontend C: mistyped namespace ######'
$dec = 'dogbuddy\web\static\js\views-decisions.js'
Copy-Item $dec (Join-Path $bak 'dec.js') -Force
try {
    Invoke-Mutate $dec 'DogBuddyViews.Decisions =' 'DogBuddyViews.Decision ='
    Test-Guard 'frontendC' @('tests/test_frontend_wiring.py')
} finally {
    Copy-Item (Join-Path $bak 'dec.js') $dec -Force
}

Write-Host '###### after restore: confirm green ######'
& $PY -m pytest tests/test_tool_whitelist.py tests/test_security_boundary.py tests/test_frontend_wiring.py -q 2>&1 | Select-Object -Last 1 | Out-Host
if ($LASTEXITCODE -ne 0) { Write-Host '  [X] not green after restore'; $script:fail = 1 }

git status --porcelain
if ($script:fail -eq 0) { Write-Host 'GUARDS ALL GREEN' } else { Write-Host 'GUARDS FAILED'; exit 1 }
