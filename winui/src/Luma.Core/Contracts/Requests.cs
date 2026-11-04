using System.Text.Json;
using System.Text.Json.Serialization;

namespace Luma.Core.Contracts;
public sealed record QueryRequest(Dictionary<string, object?>? Filters = null, string? Cursor = null, int Limit = 120);
public sealed record NeighborRequest(long ImageId, Dictionary<string, object?>? Filters = null, int Before = 4, int After = 4);
public sealed record QueryPage(JsonElement[] Items, [property:JsonPropertyName("next_cursor")] string? NextCursor, long Total);
public sealed record CachedAsset(string ResourceId, string RelativePath);
