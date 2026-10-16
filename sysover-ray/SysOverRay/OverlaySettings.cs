using System.Text.Json;

namespace SysOverRay;

public sealed class OverlaySettings
{
    public string ShortcutPath { get; set; } = Path.Combine(FindProjectRoot(), "sysover-ray", "llm-ocr-deployを起動.lnk");
    public string ProjectRoot { get; set; } = FindProjectRoot();
    public string BackendHealthUrl { get; set; } = "http://127.0.0.1:8010/api/v1/health";
    public string FrontendUrl { get; set; } = "http://127.0.0.1:5180";
    public int StartupTimeoutSeconds { get; set; } = 120;
    public int ShutdownTimeoutSeconds { get; set; } = 20;
    public int FrontendPortRangeStart { get; set; } = 5180;
    public int FrontendPortRangeEnd { get; set; } = 5180;
    public int BackendPortRangeStart { get; set; } = 8010;
    public int BackendPortRangeEnd { get; set; } = 8010;

    public static OverlaySettings Load()
    {
        var path = Path.Combine(AppContext.BaseDirectory, "overlay-settings.json");
        if (!File.Exists(path))
        {
            AppLog.Write($"Settings file not found; defaults are used: {path}");
            return new OverlaySettings();
        }

        try
        {
            var settings = JsonSerializer.Deserialize<OverlaySettings>(File.ReadAllText(path), new JsonSerializerOptions
            {
                PropertyNameCaseInsensitive = true
            });
            settings ??= new OverlaySettings();
            settings.ProjectRoot = Path.GetFullPath(settings.ProjectRoot, FindProjectRoot());
            settings.ShortcutPath = Path.GetFullPath(settings.ShortcutPath, settings.ProjectRoot);
            return settings;
        }
        catch (Exception ex)
        {
            AppLog.Write($"Could not read settings; defaults are used: {ex}");
            return new OverlaySettings();
        }
    }

    private static string FindProjectRoot()
    {
        for (var directory = new DirectoryInfo(AppContext.BaseDirectory); directory is not null; directory = directory.Parent)
        {
            if (File.Exists(Path.Combine(directory.FullName, "scripts", "dev.ps1")) &&
                Directory.Exists(Path.Combine(directory.FullName, "backend")) &&
                Directory.Exists(Path.Combine(directory.FullName, "frontend")))
            {
                return directory.FullName;
            }
        }
        return AppContext.BaseDirectory;
    }
}
