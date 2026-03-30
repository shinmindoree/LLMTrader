<#
    Runbook: Auto-start PostgreSQL Flexible Server
    Triggered every 5 minutes by Azure Automation Schedule.
    Uses System Assigned Managed Identity to authenticate.
#>

$resourceGroup = "fdpo-test-rg"
$serverName    = "fdpo-test-pgdb"

try {
    # Authenticate with System Assigned Managed Identity
    Connect-AzAccount -Identity -ErrorAction Stop | Out-Null
    Write-Output "[AutoStart] Authenticated with Managed Identity"
}
catch {
    Write-Error "[AutoStart] Failed to authenticate: $_"
    throw
}

try {
    $server = Get-AzPostgreSqlFlexibleServer -ResourceGroupName $resourceGroup -Name $serverName -ErrorAction Stop
    $state = $server.State
    Write-Output "[AutoStart] Server '$serverName' state: $state"

    if ($state -eq "Stopped") {
        Write-Output "[AutoStart] Server is stopped. Starting..."
        Start-AzPostgreSqlFlexibleServer -ResourceGroupName $resourceGroup -Name $serverName -ErrorAction Stop
        Write-Output "[AutoStart] Server start command issued successfully"
    }
    else {
        Write-Output "[AutoStart] Server is already running. No action needed."
    }
}
catch {
    Write-Error "[AutoStart] Error: $_"
    throw
}
