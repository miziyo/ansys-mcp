Set-StrictMode -Version Latest

function Get-AnsysInstallation {
    [CmdletBinding()]
    param(
        [string]$RequiredRelativePath = ""
    )

    foreach ($name in @("ANSYS_RESEARCH_ANSYS_ROOT")) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ([string]::IsNullOrWhiteSpace($value)) {
            continue
        }
        if (-not (Test-Path -LiteralPath $value -PathType Container)) {
            throw "Configured Ansys installation does not exist: $value"
        }
        $resolvedOverride = (Resolve-Path -LiteralPath $value).Path
        if (-not [string]::IsNullOrWhiteSpace($RequiredRelativePath) -and
            -not (Test-Path -LiteralPath (Join-Path $resolvedOverride $RequiredRelativePath))) {
            throw "Configured Ansys installation does not contain '$RequiredRelativePath': $resolvedOverride"
        }
        return $resolvedOverride
    }

    $candidates = [System.Collections.Generic.List[string]]::new()
    Get-ChildItem Env: | Where-Object { $_.Name -match '^AWP_ROOT\d{3}$' } | ForEach-Object {
        if (-not [string]::IsNullOrWhiteSpace($_.Value)) {
            $candidates.Add($_.Value)
        }
    }

    foreach ($variable in @("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)")) {
        $programFiles = [Environment]::GetEnvironmentVariable($variable)
        if ([string]::IsNullOrWhiteSpace($programFiles)) {
            continue
        }
        $vendor = Join-Path $programFiles "ANSYS Inc"
        foreach ($parent in @((Join-Path $vendor "ANSYS Student"), $vendor)) {
            if (Test-Path -LiteralPath $parent -PathType Container) {
                Get-ChildItem -LiteralPath $parent -Directory -Filter "v*" -ErrorAction SilentlyContinue |
                    ForEach-Object { $candidates.Add($_.FullName) }
            }
        }
    }

    $resolved = $candidates |
        Where-Object { Test-Path -LiteralPath $_ -PathType Container } |
        ForEach-Object {
            $path = (Resolve-Path -LiteralPath $_).Path
            $version = if ((Split-Path -Leaf $path) -match '^v?(\d{3})$') {
                [int]$Matches[1]
            }
            else {
                -1
            }
            [pscustomobject]@{ Path = $path; Version = $version }
        } |
        Sort-Object Version, Path -Descending |
        Group-Object { $_.Path.ToLowerInvariant() } |
        ForEach-Object { $_.Group[0] }

    foreach ($candidate in $resolved) {
        if ([string]::IsNullOrWhiteSpace($RequiredRelativePath) -or
            (Test-Path -LiteralPath (Join-Path $candidate.Path $RequiredRelativePath))) {
            return $candidate.Path
        }
    }

    $requirement = if ([string]::IsNullOrWhiteSpace($RequiredRelativePath)) {
        "an Ansys installation"
    }
    else {
        "an Ansys installation containing '$RequiredRelativePath'"
    }
    throw "Could not automatically discover $requirement. Set ANSYS_RESEARCH_ANSYS_ROOT to override discovery."
}

function Get-AnsysProductPath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$RelativePath
    )

    $installation = Get-AnsysInstallation -RequiredRelativePath $RelativePath
    return Join-Path $installation $RelativePath
}
