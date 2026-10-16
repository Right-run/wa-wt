param(
    [switch]$NoSkip          # 命令行强制：等价于交互里选择“强制重新解包/导出已完成项”（不再询问）
)

$ErrorActionPreference = "Continue"

# 本脚本必须以 UTF-8 带 BOM 保存（PS 5.1 否则按 ANSI 解码）。
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }

$ToolsDir = $PSScriptRoot
$Root     = Split-Path -Parent $ToolsDir
$OutBase  = Join-Path $Root "output"
$LogDir   = Join-Path $ToolsDir "logs"

$exe      = Join-Path $ToolsDir "config\wt_ext_cli.exe"      # vromfs 解包工具（唯一底层工具）
$exportPy = Join-Path $ToolsDir "models\export_models.py"    # 模型导出 Python 脚本
$cleanPy  = Join-Path $ToolsDir "models\_export_clean.py"    # 纯机体清理脚本（只清理飞机；其余资源原样复制）

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$stamp     = Get-Date -Format "yyyyMMdd_HHmmss"
$unpackLog = Join-Path $LogDir "unpack_$stamp.log"
$modelLog  = Join-Path $LogDir "export_$stamp.log"

# ============================================================================
#  第 0 步：选择模式 + 填写路径 + 已完成项处理
# ============================================================================
Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  资源解包工具" -ForegroundColor Cyan
Write-Host "  项目根：$Root" -ForegroundColor DarkGray
Write-Host "  日志目录：$LogDir" -ForegroundColor DarkGray
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""

# ---------- [1/4] 选择输出模式 ----------
Write-Host "[1/4] 请选择输出模式：" -ForegroundColor Yellow
Write-Host "  1 - 完整输出" -ForegroundColor Green
Write-Host "  2 - 只输出模型" -ForegroundColor Green
Write-Host "  3 - 只输出配置文件" -ForegroundColor Green
$Mode = ""
while ($Mode -notin @("1", "2", "3")) {
    $Mode = [string](Read-Host "  请输入模式编号 (1/2/3)")
}

# ---------- [2/4] 游戏目录 ----------
Write-Host ""
Write-Host "[2/4] 游戏目录" -ForegroundColor Yellow
$GameDir = ""
while ($true) {
    $GameDir = ([string](Read-Host "  游戏目录")).Trim().Trim('"')
    if ([string]::IsNullOrEmpty($GameDir)) {
        Write-Host "    输入不能为空，请重新填写。" -ForegroundColor Red
    } elseif (-not (Test-Path -LiteralPath $GameDir)) {
        Write-Host "    该目录不存在：$GameDir" -ForegroundColor Red
    } elseif (-not (Get-ChildItem -LiteralPath $GameDir -Recurse -Filter "*.vromfs.bin" -File -ErrorAction SilentlyContinue | Select-Object -First 1)) {
        Write-Host "    该目录下未找到任何 *.vromfs.bin 文件，请确认是游戏安装目录。" -ForegroundColor Red
    } else {
        break
    }
}

# ---------- [3/4] 输出目录（回车取默认） ----------
Write-Host ""
Write-Host "[3/4] 输出目录" -ForegroundColor Yellow
$OutDir = ""
while ($true) {
    $in = ([string](Read-Host "  输出目录 [回车=默认]")).Trim().Trim('"')
    if ([string]::IsNullOrEmpty($in)) { $in = $OutBase }
    try {
        New-Item -ItemType Directory -Path $in -Force -ErrorAction Stop | Out-Null
        $OutDir = $in
        break
    } catch {
        Write-Host "    无法创建该目录：$_" -ForegroundColor Red
    }
}

# ---------- [4/4] 已完成项的处理：强制重新解包/导出，还是跳过 ----------
Write-Host ""
Write-Host "[4/4] 已完成项的处理" -ForegroundColor Yellow
if ($NoSkip) {
    $ForceRedo = $true
    Write-Host "  已指定 -NoSkip：强制重新解包/重新导出已完成的项（不再询问）。" -ForegroundColor Green
} else {
    $ans = [string](Read-Host "  是否强制重新解包/重新导出已完成的项？(y=强制重做 / 回车=跳过已完成)")
    $ForceRedo = ($ans.Trim() -match '^(y|yes|是)$')
    if ($ForceRedo) {
        Write-Host "  -> 强制重做：忽略“已完成”跳过逻辑" -ForegroundColor Green
    } else {
        Write-Host "  -> 跳过已完成：已有非空结果的包/模型不再重做" -ForegroundColor DarkGray
    }
}

# ---------- 模型类型选择（仅当模式含模型时询问） ----------
$WantClean = $false
if ($Mode -in @("1", "2")) {
    Write-Host ""
    Write-Host "[*] 请选择要导出的模型类型：" -ForegroundColor Yellow
    Write-Host "  1 - 满挂载（含飞行特效面 / 挂架 / 武器 / 吊舱）" -ForegroundColor Green
    Write-Host "  2 - 纯机体（只清理飞机：剔除默认隐藏的挂架/吊舱/特效面；坦克、舰船、场景等原样保留）" -ForegroundColor Green
    $mKind = ""
    while ($mKind -notin @("1", "2")) {
        $mKind = [string](Read-Host "  请输入 (1/2，默认 1 满挂载)")
        if ([string]::IsNullOrWhiteSpace($mKind)) { $mKind = "1" }
    }
    $WantClean = ($mKind -eq "2")
}

# ---------- 汇总确认 ----------
$outRoot    = Join-Path $OutDir "config"        # 配置文件解包目录
$modelsDir  = Join-Path $OutDir "models"        # 最终模型目录（满挂载 或 纯机体）
$fullTmpDir = Join-Path $OutDir "_full_tmp"     # 纯机体模式的中转（先导满挂载再清理）
$wantConfig = $Mode -in @("1", "3")   # 是否解包配置文件
$wantModel  = $Mode -in @("1", "2")   # 是否导出模型
$totalSteps = 0
if ($wantConfig) { $totalSteps++ }
if ($wantModel)  { $totalSteps++ }
$curStep = 0

Write-Host ""
Write-Host "  输入路径（游戏目录）：$GameDir" -ForegroundColor DarkGray
Write-Host "  输出路径（输出目录）：$OutDir" -ForegroundColor DarkGray
Write-Host "  配置文件目录：$outRoot" -ForegroundColor DarkGray
Write-Host "  模型目录：    $modelsDir" -ForegroundColor DarkGray
$modeName = @{ "1" = "完整输出"; "2" = "只输出模型"; "3" = "只输出配置文件" }[$Mode]
Write-Host "  输出模式：$modeName" -ForegroundColor DarkGray
Write-Host "  已完成项：$(if ($ForceRedo) { '强制重做' } else { '跳过' })" -ForegroundColor DarkGray
Write-Host "  解包日志：$unpackLog" -ForegroundColor DarkGray
if ($wantModel) { Write-Host "  导出日志：$modelLog" -ForegroundColor DarkGray }
Write-Host ""

# ============================================================================
#  步骤 1：解包 vromfs 配置文件
# ============================================================================
$stepConfigOk = $true
$okCnt = 0; $failCnt = 0; $skipCnt = 0

if ($wantConfig) {
    $curStep++
    Write-Host "==================================================" -ForegroundColor Cyan
    Write-Host "  步骤 $curStep/$totalSteps：解包 vromfs 配置文件" -ForegroundColor Cyan
    Write-Host "==================================================" -ForegroundColor Cyan

    if (-not (Test-Path -LiteralPath $exe)) {
        Write-Host "[错误] 找不到解包工具：$exe" -ForegroundColor Red
        Write-Host "       请确认 wt_ext_cli.exe 位于 tools\config\ 下。" -ForegroundColor Yellow
        $stepConfigOk = $false
    } else {
        New-Item -ItemType Directory -Path $outRoot -Force | Out-Null
        "===== 解包开始 $(Get-Date) =====" | Out-File $unpackLog -Encoding utf8
        "游戏目录 | $GameDir" | Out-File $unpackLog -Append -Encoding utf8
        "已完成项 | $(if ($ForceRedo) { '强制重做' } else { '跳过' })" | Out-File $unpackLog -Append -Encoding utf8

        $pkgs = Get-ChildItem -LiteralPath $GameDir -Recurse -Filter "*.vromfs.bin" -File -Force -ErrorAction SilentlyContinue |
                Where-Object { $_.Length -gt 0 }
        # 归一化为规范全路径：用户可能输入短名(ADMINI~1)/相对路径，而 Get-ChildItem 返回规范路径；
        # 两者不一致会让相对名错位、"跳过已完成"失效（同一包被反复解包到不同目录）。
        $base = (Get-Item -LiteralPath $GameDir -Force).FullName.TrimEnd('\')

        foreach ($pkg in $pkgs) {
            $rel = $pkg.FullName.Substring($base.Length + 1)
            $name = $rel -replace "[\\/:*?<>|]", "_"
            $out = Join-Path $outRoot $name

            # 跳过已完成（除非选择了“强制重做”）
            if (-not $ForceRedo -and (Test-Path -LiteralPath $out)) {
                $cnt = (Get-ChildItem -LiteralPath $out -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count
                if ($cnt -gt 0) {
                    Write-Host "  跳过（已完成）：$rel" -ForegroundColor DarkGray
                    "SKIP | $rel" | Out-File $unpackLog -Append -Encoding utf8
                    $skipCnt++
                    continue
                }
            }

            $start = Get-Date
            Write-Host "  解包中：$rel ($([math]::Round($pkg.Length/1MB,2)) MB)"
            $null = & $exe --unpack_vromf -i $pkg.FullName -o $out --continue standard 2>&1
            $dur = ((Get-Date) - $start).TotalSeconds
            $status = if ($LASTEXITCODE -eq 0) { "OK" } else { "FAIL($LASTEXITCODE)" }
            if ($status -eq "OK") { $okCnt++ } else { $failCnt++; $stepConfigOk = $false }
            "$status | $rel | $([math]::Round($pkg.Length/1MB,2)) MB | ${dur}s" | Out-File $unpackLog -Append -Encoding utf8
            Write-Host "      -> $status ${dur}s" -ForegroundColor $(if ($status -eq "OK") { "Green" } else { "Red" })
        }

        "===== 解包结束：OK=$okCnt FAIL=$failCnt SKIP=$skipCnt $(Get-Date) =====" | Out-File $unpackLog -Append -Encoding utf8
        Write-Host ""
        Write-Host "  解包完成：成功=$okCnt 失败=$failCnt 跳过=$skipCnt" -ForegroundColor $(if ($failCnt -gt 0) { "Red" } else { "Green" })
        Write-Host "  日志：$unpackLog" -ForegroundColor DarkGray
    }
}

# ============================================================================
#  步骤 2：导出 3D 模型（调用 tools\models\export_models.py）
# ============================================================================
$stepModelOk = $true

if ($wantModel) {
    $curStep++
    Write-Host ""
    Write-Host "==================================================" -ForegroundColor Cyan
    Write-Host "  步骤 $curStep/$totalSteps：导出 3D 模型" -ForegroundColor Cyan
    Write-Host "==================================================" -ForegroundColor Cyan

    if (-not (Test-Path -LiteralPath $exportPy)) {
        Write-Host "[错误] 找不到模型导出脚本：$exportPy" -ForegroundColor Red
        $stepModelOk = $false
    } else {
        # 纯机体模式：先导满挂载到中转目录再清理输出到最终 models；满挂载模式：直接导到 models
        $exportTarget = if ($WantClean) { $fullTmpDir } else { $modelsDir }
        New-Item -ItemType Directory -Path $modelsDir -Force | Out-Null
        New-Item -ItemType Directory -Path $exportTarget -Force | Out-Null

        # 纯机体判定依据是各机型飞行模型 BLK 的 WeaponSlots.HideNodes（挂架/吊舱等默认隐藏节点）。
        # 「只输出模型」模式不解包配置，这里按需补解 aces.vromfs.bin（约 18 MB，数秒）；
        # 缺这份数据时清理会退化成名称规则，挂架/吊舱会残留在结果里。
        if ($WantClean) {
            $fmDir = Join-Path $outRoot "aces.vromfs.bin\aces.vromfs.bin_u\gamedata\flightmodels"
            if (-not (Test-Path -LiteralPath $fmDir)) {
                $aces = Join-Path $GameDir "aces.vromfs.bin"
                if ((Test-Path -LiteralPath $exe) -and (Test-Path -LiteralPath $aces)) {
                    Write-Host "  补解配置（纯机体判定所需）：aces.vromfs.bin" -ForegroundColor DarkGray
                    New-Item -ItemType Directory -Path $outRoot -Force | Out-Null
                    $null = & $exe --unpack_vromf -i $aces -o (Join-Path $outRoot "aces.vromfs.bin") --continue standard 2>&1
                }
                if (-not (Test-Path -LiteralPath $fmDir)) {
                    Write-Host "  [警告] 未找到飞行模型 BLK（$fmDir），纯机体只能用名称规则，挂架/吊舱可能残留" -ForegroundColor Yellow
                }
            }
        }
        Write-Host "  调用：python $exportPy --all --game-dir $GameDir --out $exportTarget" -ForegroundColor DarkGray
        Write-Host ""
        $pyArgs = @($exportPy, "--all", "--game-dir", $GameDir, "--out", $exportTarget)
        if ($ForceRedo) { $pyArgs += "--no-skip" }
        & python @pyArgs 2>&1 | Tee-Object -FilePath $modelLog
        $exportCode = $LASTEXITCODE

        if ($exportCode -ne 0) {
            Write-Host ""
            Write-Host "[失败] 模型导出脚本退出码：$exportCode" -ForegroundColor Red
            $stepModelOk = $false
        } elseif ($WantClean) {
            Write-Host ""
            Write-Host "  满挂载导出完成，开始生成纯机体（只清理飞机；坦克/舰船/场景等原样保留）..." -ForegroundColor Green
            if (-not (Test-Path -LiteralPath $cleanPy)) {
                Write-Host "  [错误] 找不到清理脚本：$cleanPy" -ForegroundColor Red
                $stepModelOk = $false
            } else {
                Write-Host "  调用：python $cleanPy --batch $fullTmpDir $modelsDir --config $outRoot" -ForegroundColor DarkGray
                Write-Host ""
                & python $cleanPy --batch $fullTmpDir $modelsDir --config $outRoot 2>&1 | Tee-Object -FilePath $modelLog -Append
                $cleanCode = $LASTEXITCODE
                if ($cleanCode -eq 0) {
                    Remove-Item -LiteralPath $fullTmpDir -Recurse -Force -ErrorAction SilentlyContinue
                    Write-Host ""
                    Write-Host "  纯机体导出完成 -> $modelsDir（中转满挂载目录已自动清理）" -ForegroundColor Green
                } else {
                    Write-Host "  [失败] 纯机体导出退出码：$cleanCode，中转目录保留：$fullTmpDir" -ForegroundColor Red
                    $stepModelOk = $false
                }
            }
        } else {
            Write-Host ""
            Write-Host "  满挂载模型导出完成 -> $modelsDir" -ForegroundColor Green
        }
        Write-Host "  日志：$modelLog" -ForegroundColor DarkGray
    }
}

# ============================================================================
#  汇总
# ============================================================================
$anyFail = ($wantConfig -and -not $stepConfigOk) -or ($wantModel -and -not $stepModelOk)

Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  执行完成" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
if ($anyFail) {
    Write-Host "  有步骤失败，请检查上方的错误信息！" -ForegroundColor Red
} else {
    Write-Host "  全部完成！" -ForegroundColor Green
}
if ($wantConfig) { Write-Host "  配置文件目录：$outRoot" -ForegroundColor DarkGray }
if ($wantModel) {
    $mType = if ($WantClean) { "纯机体（仅飞机已剔除挂架/吊舱/特效面；其他类型原样）" } else { "满挂载" }
    Write-Host "  模型目录：$modelsDir  [$mType]" -ForegroundColor DarkGray
}
Write-Host "  日志目录：$LogDir" -ForegroundColor DarkGray
Write-Host ""
Read-Host "按回车键退出"
