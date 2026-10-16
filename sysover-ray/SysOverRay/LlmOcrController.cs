using System.Diagnostics;
using System.Net.Http;

namespace SysOverRay;

public sealed class LlmOcrController
{
    private readonly OverlaySettings _settings;
    private readonly HttpClient _httpClient = new() { Timeout = TimeSpan.FromSeconds(1.5) };

    public LlmOcrController(OverlaySettings settings)
    {
        _settings = settings;
    }

    public async Task<ServiceStatus> GetStatusAsync()
    {
        var backendTask = IsAvailableAsync(_settings.BackendHealthUrl);
        var frontendTask = IsAvailableAsync(_settings.FrontendUrl);
        await Task.WhenAll(backendTask, frontendTask);
        return new ServiceStatus(await backendTask, await frontendTask);
    }

    public async Task StartAsync()
    {
        ValidateConfiguration();
        var current = await GetStatusAsync();
        if (current.BothHealthy)
        {
            AppLog.Write("Start skipped because both services are already healthy.");
            return;
        }

        await StopPrimaryPortOwnersAsync("Preparing primary ports before startup");

        AppLog.Write($"Starting through shortcut: {_settings.ShortcutPath}");
        var process = Process.Start(new ProcessStartInfo
        {
            FileName = _settings.ShortcutPath,
            WorkingDirectory = _settings.ProjectRoot,
            UseShellExecute = true,
            WindowStyle = ProcessWindowStyle.Hidden
        });

        if (process is null)
        {
            throw new InvalidOperationException("起動スクリプトを開始できませんでした。");
        }

        await WaitForStatusAsync(status => status.BothHealthy, _settings.StartupTimeoutSeconds, "起動確認がタイムアウトしました。ログを確認してください。");
        AppLog.Write("Both services are healthy.");
    }

    public async Task StopAsync()
    {
        ValidateConfiguration();
        var scriptPath = Path.Combine(AppContext.BaseDirectory, "scripts", "Stop-LlmOcr.ps1");
        if (!File.Exists(scriptPath))
        {
            throw new FileNotFoundException("停止スクリプトが見つかりません。", scriptPath);
        }

        AppLog.Write("Stopping project processes.");
        var startInfo = new ProcessStartInfo
        {
            FileName = "powershell.exe",
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true
        };
        startInfo.ArgumentList.Add("-NoProfile");
        startInfo.ArgumentList.Add("-ExecutionPolicy");
        startInfo.ArgumentList.Add("Bypass");
        startInfo.ArgumentList.Add("-File");
        startInfo.ArgumentList.Add(scriptPath);
        startInfo.ArgumentList.Add("-ProjectRoot");
        startInfo.ArgumentList.Add(_settings.ProjectRoot);
        startInfo.ArgumentList.Add("-BackendPort");
        startInfo.ArgumentList.Add(new Uri(_settings.BackendHealthUrl).Port.ToString());

        using var process = Process.Start(startInfo) ?? throw new InvalidOperationException("停止処理を開始できませんでした。");
        var outputTask = process.StandardOutput.ReadToEndAsync();
        var errorTask = process.StandardError.ReadToEndAsync();
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(_settings.ShutdownTimeoutSeconds));
        try
        {
            await process.WaitForExitAsync(timeout.Token);
        }
        catch (OperationCanceledException)
        {
            try { process.Kill(true); } catch { }
            throw new TimeoutException("停止処理がタイムアウトしました。");
        }

        var output = (await outputTask).Trim();
        var error = (await errorTask).Trim();
        if (!string.IsNullOrWhiteSpace(output))
        {
            AppLog.Write($"Stop output: {output}");
        }

        if (process.ExitCode != 0)
        {
            throw new InvalidOperationException(string.IsNullOrWhiteSpace(error) ? $"停止処理に失敗しました（終了コード {process.ExitCode}）。" : error);
        }

        await StopPrimaryPortOwnersAsync("Cleaning primary ports after project stop");
        await WaitForStatusAsync(status => !status.AnyHealthy, _settings.ShutdownTimeoutSeconds, "プロセス停止後もポートが応答しています。");
        AppLog.Write("Both services are stopped.");
    }

    public async Task RestartAsync()
    {
        await StopAsync();
        await Task.Delay(500);
        await StartAsync();
    }

    public async Task<string> StopManagedPortsAsync()
    {
        ValidateConfiguration();
        AppLog.Write($"Stopping managed port ranges: frontend {_settings.FrontendPortRangeStart}-{_settings.FrontendPortRangeEnd}, backend {_settings.BackendPortRangeStart}-{_settings.BackendPortRangeEnd}.");
        await StopPortRangesAsync(
            _settings.FrontendPortRangeStart,
            _settings.FrontendPortRangeEnd,
            _settings.BackendPortRangeStart,
            _settings.BackendPortRangeEnd,
            "Managed port stop");
        await WaitForStatusAsync(status => !status.AnyHealthy, _settings.ShutdownTimeoutSeconds, "関連ポート停止後も標準ポートが応答しています。");
        AppLog.Write("All configured development ports are stopped.");
        return "関連ポートを全停止しました";
    }

    private Task<string> StopPrimaryPortOwnersAsync(string logLabel)
    {
        var frontendPort = new Uri(_settings.FrontendUrl).Port;
        var backendPort = new Uri(_settings.BackendHealthUrl).Port;
        return StopPortRangesAsync(frontendPort, frontendPort, backendPort, backendPort, logLabel, projectOnly: true);
    }

    private async Task<string> StopPortRangesAsync(
        int frontendPortStart,
        int frontendPortEnd,
        int backendPortStart,
        int backendPortEnd,
        string logLabel,
        bool projectOnly = false)
    {
        var scriptPath = Path.Combine(AppContext.BaseDirectory, "scripts", "Stop-LlmOcrPorts.ps1");
        if (!File.Exists(scriptPath))
        {
            throw new FileNotFoundException("ポート停止スクリプトが見つかりません。", scriptPath);
        }

        var startInfo = new ProcessStartInfo
        {
            FileName = "powershell.exe",
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true
        };
        startInfo.ArgumentList.Add("-NoProfile");
        startInfo.ArgumentList.Add("-ExecutionPolicy");
        startInfo.ArgumentList.Add("Bypass");
        startInfo.ArgumentList.Add("-File");
        startInfo.ArgumentList.Add(scriptPath);
        startInfo.ArgumentList.Add("-FrontendPortStart");
        startInfo.ArgumentList.Add(frontendPortStart.ToString());
        startInfo.ArgumentList.Add("-FrontendPortEnd");
        startInfo.ArgumentList.Add(frontendPortEnd.ToString());
        startInfo.ArgumentList.Add("-BackendPortStart");
        startInfo.ArgumentList.Add(backendPortStart.ToString());
        startInfo.ArgumentList.Add("-BackendPortEnd");
        startInfo.ArgumentList.Add(backendPortEnd.ToString());
        if (projectOnly)
        {
            startInfo.ArgumentList.Add("-ProjectRoot");
            startInfo.ArgumentList.Add(_settings.ProjectRoot);
        }

        using var process = Process.Start(startInfo) ?? throw new InvalidOperationException("ポート停止処理を開始できませんでした。");
        var outputTask = process.StandardOutput.ReadToEndAsync();
        var errorTask = process.StandardError.ReadToEndAsync();
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(_settings.ShutdownTimeoutSeconds));
        try
        {
            await process.WaitForExitAsync(timeout.Token);
        }
        catch (OperationCanceledException)
        {
            try { process.Kill(true); } catch { }
            throw new TimeoutException("ポート停止処理がタイムアウトしました。");
        }

        var output = (await outputTask).Trim();
        var error = (await errorTask).Trim();
        if (!string.IsNullOrWhiteSpace(output))
        {
            AppLog.Write($"{logLabel} output: {output}");
        }
        if (process.ExitCode != 0)
        {
            throw new InvalidOperationException(string.IsNullOrWhiteSpace(error) ? $"ポート停止処理に失敗しました（終了コード {process.ExitCode}）。" : error);
        }
        return output;
    }

    private async Task<bool> IsAvailableAsync(string url)
    {
        try
        {
            using var response = await _httpClient.GetAsync(url);
            return response.IsSuccessStatusCode;
        }
        catch
        {
            return false;
        }
    }

    private async Task WaitForStatusAsync(Func<ServiceStatus, bool> predicate, int timeoutSeconds, string timeoutMessage)
    {
        var deadline = DateTime.UtcNow.AddSeconds(timeoutSeconds);
        while (DateTime.UtcNow < deadline)
        {
            var status = await GetStatusAsync();
            if (predicate(status))
            {
                return;
            }

            await Task.Delay(500);
        }

        throw new TimeoutException(timeoutMessage);
    }

    private void ValidateConfiguration()
    {
        if (!File.Exists(_settings.ShortcutPath))
        {
            throw new FileNotFoundException("起動ショートカットが見つかりません。", _settings.ShortcutPath);
        }

        if (!Directory.Exists(_settings.ProjectRoot))
        {
            throw new DirectoryNotFoundException($"プロジェクトフォルダーが見つかりません: {_settings.ProjectRoot}");
        }
    }
}

public readonly record struct ServiceStatus(bool BackendHealthy, bool FrontendHealthy)
{
    public bool BothHealthy => BackendHealthy && FrontendHealthy;
    public bool AnyHealthy => BackendHealthy || FrontendHealthy;
}
