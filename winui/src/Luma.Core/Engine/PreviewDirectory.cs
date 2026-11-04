using System.Text.Json;
namespace Luma.Core.Engine;
public static class PreviewDirectory
{
    public static string Create(string parent)
    {
        if (!Path.IsPathFullyQualified(parent)) throw new EngineRpcException("unsafe_data_directory");
        EngineProcess.RejectReparse(parent);
        string path = Path.Combine(parent, "engine-probe-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(path);
        using var marker = new FileStream(Path.Combine(path, ".luma-engine-preview.json"), FileMode.CreateNew);
        JsonSerializer.Serialize(marker, new { schema = 1, purpose = "luma-stage3-preview" });
        return path;
    }
}
