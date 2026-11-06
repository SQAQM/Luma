using System.Text;
using System.Text.Json;
using Luma.Core.Engine;

if(args.Length==1&&args[0]=="--slideshow-only")
{
 try{Console.WriteLine("SLIDESHOW_PASSED "+SlideshowTests.Run());return 0;}
 catch(Exception error){Console.Error.WriteLine(error);return 1;}
}

if(args.Length==1&&args[0]=="--shell-reveal-only")
{
 try{Console.WriteLine("SHELL_REVEAL_PASSED "+ShellRevealTests.Run());return 0;}
 catch(Exception error){Console.Error.WriteLine(error);return 1;}
}
if(args.Length==1&&args[0]=="--file-review-only")
{
 try{Console.WriteLine("FILE_REVIEW_PASSED "+FileReviewTests.Run());return 0;}
 catch(Exception error){Console.Error.WriteLine(error);return 1;}
}
if(args.Length==1&&args[0]=="--storage-only")
{
 try{Console.WriteLine("STORAGE_PASSED "+StorageTests.Run());return 0;}
 catch(Exception error){Console.Error.WriteLine(error);return 1;}
}

if(args.Length==1&&args[0]=="--civitai-only")
{
 try{Console.WriteLine("CIVITAI_FEEDBACK_PASSED "+await CivitaiFeedbackTests.Run());return 0;}
 catch(Exception error){Console.Error.WriteLine(error);return 1;}
}

if(args.Length==1&&args[0]=="--model-directory-only")
{
 try{Console.WriteLine("MODEL_DIRECTORY_PASSED "+await ModelDirectoryAddTests.Run());return 0;}
 catch(Exception error){Console.Error.WriteLine("MODEL_DIRECTORY_FAILED "+error);return 1;}
}
if(args.Length == 2 && args[0] == "--data-dir") return await FakeEngine.Run(args[1]);
if(args.Length==3&&args[0] is "--cache-preview" or "--cache-apply")
{
 try
 {
  if(args[0]=="--cache-preview"){var plan=Luma.Core.Storage.CacheCleanup.Preview(Path.GetFullPath(args[1]));using var stream=new FileStream(args[2],FileMode.CreateNew);JsonSerializer.Serialize(stream,plan);Console.WriteLine($"CACHE_PREVIEW {plan.Files.Count} {plan.Bytes} {plan.More}");}
  else{var plan=JsonSerializer.Deserialize<Luma.Core.Storage.CachePlan>(File.ReadAllText(args[1]))!;var result=Luma.Core.Storage.CacheCleanup.Apply(plan);using var stream=new FileStream(args[2],FileMode.CreateNew);JsonSerializer.Serialize(stream,result);Console.WriteLine($"CACHE_APPLIED {result.Removed} {result.Skipped} {result.Bytes}");}
  return 0;
 }catch(Exception error){Console.Error.WriteLine("CACHE_REFUSED "+error.GetType().Name);return 1;}
}
if(args.Length==4&&args[0]=="--storage-fixture")
{
 try{var plan=Luma.Core.Storage.ProfileSnapshots.Preview(Path.GetFullPath(args[1]));string saved=Luma.Core.Storage.ProfileSnapshots.Create(plan,Path.GetFullPath(args[2]));string restored=Luma.Core.Storage.ProfileSnapshots.Restore(saved,Path.GetFullPath(args[3]));Console.WriteLine(JsonSerializer.Serialize(new{restored,files=plan.Files.Count,bytes=plan.Bytes}));return 0;}
 catch(Exception error){Console.Error.WriteLine("STORAGE_REFUSED "+error);return 1;}
}
if(args.Length==3&&args[0]=="--management-only"){try{Console.WriteLine("MANAGEMENT_INTEGRATION_PASSED "+await ManagementIntegration.Run(Path.GetFullPath(args[1]),Path.GetFullPath(args[2])));return 0;}catch(Exception error){Console.Error.WriteLine(error.GetType().Name+" "+error.Message+"\n"+error.StackTrace);return 1;}}

int passed = 0;
void Check(bool condition, string name) { if (!condition) throw new Exception("CORE_FAIL " + name); passed++; }
async Task Reject(byte[] data) { try { await FrameCodec.ReadAsync(new MemoryStream(data)); throw new Exception("invalid frame accepted"); } catch (EngineProtocolException) { passed++; } }
try
{
    using var vectors = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "vectors.json")));
    foreach (var value in vectors.RootElement.GetProperty("valid").EnumerateArray())
    {
        byte[] data = Encoding.UTF8.GetBytes(value.GetString()!);
        using var result = await FrameCodec.ReadAsync(new MemoryStream(BitConverter.GetBytes(data.Length).Concat(data).ToArray()));
        Check(result is not null, "shared valid vector");
    }
    foreach (var value in vectors.RootElement.GetProperty("invalid").EnumerateArray())
    { byte[] data = Encoding.UTF8.GetBytes(value.GetString()!); await Reject(BitConverter.GetBytes(data.Length).Concat(data).ToArray()); }
    foreach (var value in vectors.RootElement.GetProperty("invalidUtf8Hex").EnumerateArray())
    { byte[] data = Convert.FromHexString(value.GetString()!); await Reject(BitConverter.GetBytes(data.Length).Concat(data).ToArray()); }
    await Reject([1]); await Reject(BitConverter.GetBytes(8).Concat("{}"u8.ToArray()).ToArray()); await Reject(BitConverter.GetBytes(0)); await Reject(BitConverter.GetBytes(FrameCodec.MaxFrame + 1));
    using(var fragmented = new FragmentedStream(FrameCodec.Encode(new { sample="雪😀" }).Concat(FrameCodec.Encode(new { sample="two" })).ToArray()))
    { using var first=await FrameCodec.ReadAsync(fragmented);using var second=await FrameCodec.ReadAsync(fragmented);Check(first!.RootElement.GetProperty("sample").GetString()=="雪😀" && second!.RootElement.GetProperty("sample").GetString()=="two","fragmented coalesced stream");Check(await FrameCodec.ReadAsync(fragmented) is null,"clean frame EOF"); }
    var payload = new { prompt = string.Concat(Enumerable.Repeat("😀", 512*1024)), negative_prompt = string.Concat(Enumerable.Repeat("😀", 512*1024)), parameters = new { raw = new string('界', 1024*1024) } };
    byte[] framed = FrameCodec.Encode(payload);
    using var full = await FrameCodec.ReadAsync(new MemoryStream(framed));
    Check(full!.RootElement.GetProperty("prompt").GetString() == payload.prompt, "worst Unicode prompt lossless");
    Check(full.RootElement.GetProperty("parameters").GetProperty("raw").GetString() == payload.parameters.raw, "raw metadata lossless");
    try { FrameCodec.Encode(new { invalid = "\ud800" }); throw new Exception("outbound invalid UTF16 was silently replaced"); } catch(EngineProtocolException) { passed++; }
    passed += await GalleryTests.Run();
    passed += ViewerTests.Run();
    passed += SlideshowTests.Run();
    passed += SequenceTests.Run();
    passed += ModelSyncTests.Run();
    passed += await ViewerNavigationTests.Run();
    passed += ViewerAssetTests.Run();
    passed += await ViewerReviewTests.Run();
    passed += await ManagementTests.Run();
    passed += await ModelDirectoryAddTests.Run();
    passed += FileReviewTests.Run();
    passed += MaterialTests.Run();
    passed += StorageTests.Run();
    passed += CacheCleanupTests.Run();
    passed += await EngineLifecycleTests.Run();
    if(args.Length == 4 && args[0] == "--engine" && args[2] == "--output")
    {
        passed += await IntegrationSuite.Run(Path.GetFullPath(args[1]), Path.GetFullPath(args[3]));
        passed += await GalleryIntegration.Run(Path.GetFullPath(args[1]),Path.Combine(Path.GetFullPath(args[3]),"gallery"));
        passed += await ViewerIntegration.Run(Path.GetFullPath(args[1]),Path.Combine(Path.GetFullPath(args[3]),"viewer"));
        passed += await ManagementIntegration.Run(Path.GetFullPath(args[1]),Path.Combine(Path.GetFullPath(args[3]),"management"));
    }
    Console.WriteLine("CORE_PASSED " + passed);
    return 0;
}
catch (Exception error) { Console.Error.WriteLine("CORE_FAILED " + error); return 1; }

sealed class FragmentedStream(byte[] data) : MemoryStream(data)
{ public override ValueTask<int> ReadAsync(Memory<byte> buffer,CancellationToken cancellationToken=default) => base.ReadAsync(buffer[..Math.Min(buffer.Length,3)],cancellationToken); }
