using System.Text.Json;
using Luma.Core.Engine;

internal static class FakeEngine
{
    public static async Task<int> Run(string directory)
    {
        string mode=File.ReadAllText(Path.Combine(File.Exists(Path.Combine(directory,"fake-mode.txt")) ? directory : AppContext.BaseDirectory,"fake-mode.txt"));
        using var input=Console.OpenStandardInput();using var output=Console.OpenStandardOutput();
        while(true)
        {
            using var document=await FrameCodec.ReadAsync(input);if(document is null)return 0;
            var root=document.RootElement;string method=root.GetProperty("method").GetString()!;
            object payload=method=="engine.hello" ? new{protocol=1,preview=true,writesEnabled=false,maxFrameBytes=FrameCodec.MaxFrame} : new{syntheticSecret="not-for-publication"} as object;
            if(method=="catalog.query")payload=new{items=Array.Empty<object>(),next_cursor=(string?)null,total=0};
            int version=mode=="version" ? 2 : 1;
            long generation=root.GetProperty("generation").GetInt64()+(mode=="context" && method!="engine.hello" ? 1 : 0);
            var reply=new{version,requestId=root.GetProperty("requestId").GetString(),generation,identity=root.GetProperty("identity").GetString(),kind="result",payload};
            if(mode=="idle" && method=="catalog.query")await Task.Delay(1000);
            await output.WriteAsync(FrameCodec.Encode(reply));await output.FlushAsync();
            if(mode=="idle" && method=="catalog.query"){await Task.Delay(1500);return 0;}
            if(method=="engine.shutdown")return 0;
        }
    }
}
