using System.Buffers.Binary;
using System.Text;
using System.Text.Json;
using System.Text.Encodings.Web;
using System.Text.Json.Serialization;

namespace Luma.Core.Engine;

public sealed class EngineProtocolException(string code = "protocol_error") : IOException(code)
{
    public string Code { get; } = code;
}

public static class FrameCodec
{
    public const int MaxFrame = 64 * 1024 * 1024;
    private static readonly UTF8Encoding StrictUtf8 = new(false, true);
    private sealed class StrictString : JsonConverter<string>
    {
        public override string? Read(ref Utf8JsonReader reader, Type type, JsonSerializerOptions options) { string? value=reader.GetString(); if(value is not null) StrictUtf8.GetByteCount(value); return value; }
        public override void Write(Utf8JsonWriter writer,string value,JsonSerializerOptions options) { StrictUtf8.GetByteCount(value);writer.WriteStringValue(value); }
        public override void WriteAsPropertyName(Utf8JsonWriter writer,string value,JsonSerializerOptions options) { StrictUtf8.GetByteCount(value);writer.WritePropertyName(value); }
    }
    internal static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase, Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping, MaxDepth = 64, Converters = { new StrictString() } };

    public static byte[] Encode<T>(T value)
    {
        try
        {
            byte[] body = JsonSerializer.SerializeToUtf8Bytes(value, Json);
            if (body.Length is < 1 or > MaxFrame) throw new EngineProtocolException("request_too_large");
            using var document = Parse(body);
            byte[] result = new byte[body.Length + 4];
            BinaryPrimitives.WriteUInt32LittleEndian(result, (uint)body.Length);
            body.CopyTo(result.AsSpan(4)); return result;
        }
        catch (Exception error) when(error is JsonException or EncoderFallbackException or NotSupportedException) { throw new EngineProtocolException(); }
    }

    private static JsonDocument Parse(byte[] body)
    {
        JsonDocument? document = null;
        try
        {
            StrictUtf8.GetCharCount(body);
            document = JsonDocument.Parse(body, new JsonDocumentOptions { MaxDepth = 64 });
            if (document.RootElement.ValueKind != JsonValueKind.Object) throw new EngineProtocolException();
            var stack = new Stack<JsonElement>(); stack.Push(document.RootElement);
            int count = 0;
            while (stack.TryPop(out var value))
            {
                if (++count > 500_000) throw new EngineProtocolException();
                if (value.ValueKind == JsonValueKind.Object)
                {
                    var names = new HashSet<string>(StringComparer.Ordinal);
                    foreach (var property in value.EnumerateObject())
                    { if (!names.Add(property.Name)) throw new EngineProtocolException(); StrictUtf8.GetByteCount(property.Name); stack.Push(property.Value); }
                }
                else if (value.ValueKind == JsonValueKind.Array) foreach (var item in value.EnumerateArray()) stack.Push(item);
                else if (value.ValueKind == JsonValueKind.String) StrictUtf8.GetByteCount(value.GetString()!);
                else if (value.ValueKind == JsonValueKind.Number && !value.TryGetInt64(out _))
                {
                    string raw = value.GetRawText();
                    if (raw.IndexOfAny(['.', 'e', 'E']) < 0 || !double.IsFinite(value.GetDouble())) throw new EngineProtocolException();
                }
            }
            return document;
        }
        catch (Exception error) when (error is JsonException or DecoderFallbackException or EncoderFallbackException or InvalidOperationException or FormatException)
        { document?.Dispose(); throw new EngineProtocolException(); }
        catch { document?.Dispose(); throw; }
    }

    public static async ValueTask<JsonDocument?> ReadAsync(Stream input, CancellationToken cancellationToken = default)
    {
        byte[] header = new byte[4];
        int first = await input.ReadAsync(header, cancellationToken).ConfigureAwait(false);
        if (first == 0) return null;
        try
        {
            if (first < 4) await input.ReadExactlyAsync(header.AsMemory(first), cancellationToken).ConfigureAwait(false);
            uint length = BinaryPrimitives.ReadUInt32LittleEndian(header);
            if (length is < 1 or > MaxFrame) throw new EngineProtocolException();
            byte[] body = new byte[length];
            await input.ReadExactlyAsync(body, cancellationToken).ConfigureAwait(false);
            return Parse(body);
        }
        catch (EndOfStreamException) { throw new EngineProtocolException(); }
    }
}
