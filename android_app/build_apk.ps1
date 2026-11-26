<#
.SYNOPSIS
    Zero-Docker Native Windows Builder for NetViz Survey Android APK.
.DESCRIPTION
    Auto-detects or bootstraps portable Eclipse Temurin JDK 17, Gradle 8.11.1,
    and Android SDK Command-Line Tools (API 35) into .toolchains/, runs unit
    tests, builds the APK, and copies it into dist/.
#>
param(
    [switch]$SkipTests,
    [switch]$Release,
    [switch]$Install,
    [switch]$SetupUsbSync
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$AndroidAppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $AndroidAppDir
$ToolchainsDir = Join-Path $RepoRoot ".toolchains"
$DistDir = Join-Path $RepoRoot "dist"

New-Item -ItemType Directory -Force -Path $ToolchainsDir | Out-Null
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

function Write-Step {
    param([string]$Message)
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Invoke-DownloadFile {
    param(
        [string]$Url,
        [string]$OutFile
    )
    Write-Host "    Downloading: $Url"
    if (Test-Path $OutFile) {
        Remove-Item -Force $OutFile
    }
    $curlCmd = Get-Command "curl.exe" -ErrorAction SilentlyContinue
    if ($null -ne $curlCmd) {
        $prevPref = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & $curlCmd.Source -fSL --retry 3 --connect-timeout 20 -o $OutFile $Url
        $code = $LASTEXITCODE
        $ErrorActionPreference = $prevPref
        if ($code -eq 0 -and (Test-Path $OutFile) -and ((Get-Item $OutFile).Length -gt 0)) {
            return
        }
    }
    Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing
}

function Get-JavaMajorVersion {
    param([string]$JavaExe)
    if (-not (Test-Path $JavaExe)) {
        return 0
    }
    $prevPref = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $output = & $JavaExe -version 2>&1 | Out-String
    $ErrorActionPreference = $prevPref
    if ($output -match 'version\s+"(\d+)(?:\.(\d+))?') {
        $first = [int]$Matches[1]
        if ($first -eq 1 -and $Matches[2]) {
            return [int]$Matches[2]
        }
        return $first
    }
    return 0
}

function Resolve-Jdk17Home {
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($env:JAVA_HOME) {
        $candidates.Add($env:JAVA_HOME)
    }
    $cachedDirs = Get-ChildItem -Path $ToolchainsDir -Directory -Filter "jdk-17*" -ErrorAction SilentlyContinue
    foreach ($dir in $cachedDirs) {
        $candidates.Add($dir.FullName)
    }
    $whereJava = Get-Command "java.exe" -ErrorAction SilentlyContinue
    if ($null -ne $whereJava) {
        $candidates.Add((Split-Path -Parent (Split-Path -Parent $whereJava.Source)))
    }

    foreach ($cand in $candidates) {
        if (-not $cand) { continue }
        $javaExe = Join-Path $cand "bin\java.exe"
        $major = Get-JavaMajorVersion -JavaExe $javaExe
        if ($major -ge 17 -and $major -le 21) {
            return $cand
        } elseif ($major -gt 0) {
            Write-Host "    Skipping Java $major at $cand (need JDK 17..21)" -ForegroundColor Yellow
        }
    }

    Write-Step "Bootstrapping portable Eclipse Temurin JDK 17 into .toolchains/jdk-17..."
    $jdkZip = Join-Path $ToolchainsDir "temurin-jdk17.zip"
    $jdkStage = Join-Path $ToolchainsDir "jdk-17-stage"
    $jdkTarget = Join-Path $ToolchainsDir "jdk-17"
    $adoptiumUrl = "https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse"

    Invoke-DownloadFile -Url $adoptiumUrl -OutFile $jdkZip
    if (Test-Path $jdkStage) { Remove-Item -Recurse -Force $jdkStage }
    if (Test-Path $jdkTarget) { Remove-Item -Recurse -Force $jdkTarget }

    Expand-Archive -Path $jdkZip -DestinationPath $jdkStage -Force
    $inner = Get-ChildItem -Path $jdkStage -Directory | Select-Object -First 1
    if ($null -eq $inner) {
        throw "Extracted Temurin JDK archive was empty."
    }
    Move-Item -Path $inner.FullName -Destination $jdkTarget
    Remove-Item -Recurse -Force $jdkStage
    Remove-Item -Force $jdkZip

    $verifiedMajor = Get-JavaMajorVersion -JavaExe (Join-Path $jdkTarget "bin\java.exe")
    if ($verifiedMajor -lt 17) {
        throw "Bootstrapped JDK verification failed (detected major version $verifiedMajor)."
    }
    return $jdkTarget
}

function Resolve-GradleBin {
    $gradleRoot = Join-Path $ToolchainsDir "gradle-8.11.1"
    $gradleBat = Join-Path $gradleRoot "bin\gradle.bat"
    if (Test-Path $gradleBat) {
        return $gradleBat
    }

    Write-Step "Bootstrapping Gradle 8.11.1 into .toolchains/gradle-8.11.1..."
    $gradleZip = Join-Path $ToolchainsDir "gradle-8.11.1-bin.zip"
    $gradleUrl = "https://services.gradle.org/distributions/gradle-8.11.1-bin.zip"
    Invoke-DownloadFile -Url $gradleUrl -OutFile $gradleZip
    Expand-Archive -Path $gradleZip -DestinationPath $ToolchainsDir -Force
    Remove-Item -Force $gradleZip

    if (-not (Test-Path $gradleBat)) {
        throw "Gradle 8.11.1 binary missing after extraction: $gradleBat"
    }
    return $gradleBat
}

function Resolve-AndroidSdkRoot {
    $sdkCandidates = New-Object System.Collections.Generic.List[string]
    if ($env:ANDROID_HOME) { $sdkCandidates.Add($env:ANDROID_HOME) }
    if ($env:ANDROID_SDK_ROOT) { $sdkCandidates.Add($env:ANDROID_SDK_ROOT) }
    if ($env:LOCALAPPDATA) { $sdkCandidates.Add((Join-Path $env:LOCALAPPDATA "Android\Sdk")) }
    $localSdk = Join-Path $ToolchainsDir "android-sdk"
    $sdkCandidates.Add($localSdk)

    $chosenRoot = $null
    foreach ($cand in $sdkCandidates) {
        if (-not $cand) { continue }
        $hasPlatform = Test-Path (Join-Path $cand "platforms\android-35")
        $hasBuildTools = Test-Path (Join-Path $cand "build-tools\35.0.0")
        $hasCmdline = (Test-Path (Join-Path $cand "cmdline-tools\latest\bin\sdkmanager.bat")) -or
                      (Test-Path (Join-Path $cand "cmdline-tools\bin\sdkmanager.bat"))
        if ($hasPlatform -and $hasBuildTools) {
            return $cand
        }
        if ($hasCmdline -and $null -eq $chosenRoot) {
            $chosenRoot = $cand
        }
    }

    if ($null -eq $chosenRoot) {
        $chosenRoot = $localSdk
    }
    New-Item -ItemType Directory -Force -Path $chosenRoot | Out-Null

    $sdkManager = Join-Path $chosenRoot "cmdline-tools\latest\bin\sdkmanager.bat"
    if (-not (Test-Path $sdkManager)) {
        $altMgr = Join-Path $chosenRoot "cmdline-tools\bin\sdkmanager.bat"
        if (Test-Path $altMgr) {
            $sdkManager = $altMgr
        }
    }

    if (-not (Test-Path $sdkManager)) {
        Write-Step "Bootstrapping Android SDK Command-Line Tools into $chosenRoot..."
        $cmdZip = Join-Path $ToolchainsDir "commandlinetools-win.zip"
        $cmdUrl = "https://dl.google.com/android/repository/commandlinetools-win-15859902_latest.zip"
        try {
            Invoke-DownloadFile -Url $cmdUrl -OutFile $cmdZip
        } catch {
            Write-Host "    Primary URL failed, resolving latest commandlinetools-win URL from developer.android.com..." -ForegroundColor Yellow
            $studioHtml = (Invoke-WebRequest -Uri "https://developer.android.com/studio" -UseBasicParsing).Content
            if ($studioHtml -match 'https://dl\.google\.com/android/repository/commandlinetools-win-\d+_latest\.zip') {
                $cmdUrl = $Matches[0]
                Invoke-DownloadFile -Url $cmdUrl -OutFile $cmdZip
            } else {
                throw "Unable to locate Android Command-Line Tools download URL."
            }
        }

        $cmdStage = Join-Path $ToolchainsDir "cmdline-stage"
        if (Test-Path $cmdStage) { Remove-Item -Recurse -Force $cmdStage }
        Expand-Archive -Path $cmdZip -DestinationPath $cmdStage -Force
        Remove-Item -Force $cmdZip

        $latestDir = Join-Path $chosenRoot "cmdline-tools\latest"
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $latestDir) | Out-Null
        if (Test-Path $latestDir) { Remove-Item -Recurse -Force $latestDir }

        $innerCmd = Join-Path $cmdStage "cmdline-tools"
        if (Test-Path $innerCmd) {
            Move-Item -Path $innerCmd -Destination $latestDir
        } else {
            Move-Item -Path $cmdStage -Destination $latestDir
        }
        if (Test-Path $cmdStage) { Remove-Item -Recurse -Force $cmdStage }
        $sdkManager = Join-Path $latestDir "bin\sdkmanager.bat"
    }

    if (-not (Test-Path $sdkManager)) {
        throw "sdkmanager.bat not found at $sdkManager"
    }

    $needsInstall = (-not (Test-Path (Join-Path $chosenRoot "platforms\android-35"))) -or
                    (-not (Test-Path (Join-Path $chosenRoot "build-tools\35.0.0"))) -or
                    (-not (Test-Path (Join-Path $chosenRoot "platform-tools\adb.exe")))

    if ($needsInstall) {
        Write-Step "Accepting Android SDK licenses and installing API 35 packages..."
        $prevPref = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $yesFeed = ("y`n" * 12)
        $yesFeed | & $sdkManager "--sdk_root=$chosenRoot" --licenses | Out-Null
        & $sdkManager "--sdk_root=$chosenRoot" "platform-tools" "platforms;android-35" "build-tools;35.0.0"
        $sdkExit = $LASTEXITCODE
        $ErrorActionPreference = $prevPref
        if ($sdkExit -ne 0) {
            throw "sdkmanager failed with exit code $sdkExit"
        }
    }

    return $chosenRoot
}

Write-Step "Resolving JDK 17+ toolchain..."
$JdkHome = Resolve-Jdk17Home
$env:JAVA_HOME = $JdkHome
$env:PATH = "$(Join-Path $JdkHome 'bin');$env:PATH"
Write-Host "    JAVA_HOME = $JdkHome"

Write-Step "Resolving Gradle 8.11.1..."
$GradleBat = Resolve-GradleBin
Write-Host "    GRADLE    = $GradleBat"

Write-Step "Resolving Android SDK (API 35)..."
$SdkRoot = Resolve-AndroidSdkRoot
$env:ANDROID_HOME = $SdkRoot
$env:ANDROID_SDK_ROOT = $SdkRoot
Write-Host "    SDK_ROOT  = $SdkRoot"

# Escape backslash/slash and colon for Java .properties format
$EscapedSdkDir = ($SdkRoot -replace '\\', '/') -replace ':', '\:'
$LocalPropsPath = Join-Path $AndroidAppDir "local.properties"
[System.IO.File]::WriteAllText($LocalPropsPath, "sdk.dir=$EscapedSdkDir`n", [System.Text.Encoding]::ASCII)

Push-Location $AndroidAppDir
try {
    if (-not $SkipTests) {
        Write-Step "Running Android JUnit tests (testDebugUnitTest)..."
        & $GradleBat testDebugUnitTest --no-daemon
        if ($LASTEXITCODE -ne 0) {
            throw "Android unit tests failed with exit code $LASTEXITCODE"
        }
    }

    $GradleTask = if ($Release) { "assembleRelease" } else { "assembleDebug" }
    Write-Step "Building Android APK ($GradleTask)..."
    & $GradleBat $GradleTask --no-daemon
    if ($LASTEXITCODE -ne 0) {
        throw "Gradle $GradleTask failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

if ($Release) {
    $BuiltApk = Get-ChildItem -Path (Join-Path $AndroidAppDir "app\build\outputs\apk\release") -Filter "*.apk" | Select-Object -First 1
    $DestApk = Join-Path $DistDir "NetVizSurvey-release.apk"
} else {
    $BuiltApk = Get-Item (Join-Path $AndroidAppDir "app\build\outputs\apk\debug\app-debug.apk")
    $DestApk = Join-Path $DistDir "NetVizSurvey-debug.apk"
}

if ($null -eq $BuiltApk -or -not (Test-Path $BuiltApk.FullName)) {
    throw "Expected APK output was not produced."
}

Copy-Item -Path $BuiltApk.FullName -Destination $DestApk -Force
$ApkInfo = Get-Item $DestApk
$Sha256 = (Get-FileHash -Path $DestApk -Algorithm SHA256).Hash.ToLowerInvariant()

Write-Step "APK Build Complete!"
Write-Host "    Output : $DestApk"
Write-Host "    Size   : $($ApkInfo.Length) bytes ($([math]::Round($ApkInfo.Length / 1MB, 2)) MB)"
Write-Host "    SHA256 : $Sha256"

$AdbExe = Join-Path $SdkRoot "platform-tools\adb.exe"
if ($Install) {
    if (-not (Test-Path $AdbExe)) { throw "adb.exe not found at $AdbExe" }
    Write-Step "Installing APK onto connected Android device..."
    & $AdbExe install -r $DestApk
    if ($LASTEXITCODE -ne 0) { throw "adb install failed with exit code $LASTEXITCODE" }
}

if ($SetupUsbSync) {
    if (-not (Test-Path $AdbExe)) { throw "adb.exe not found at $AdbExe" }
    Write-Step "Setting up USB ADB reverse tunnel (tcp:8765 -> tcp:8765)..."
    & $AdbExe reverse tcp:8765 tcp:8765
    if ($LASTEXITCODE -ne 0) { throw "adb reverse failed with exit code $LASTEXITCODE" }
}
