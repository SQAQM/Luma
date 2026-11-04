using System.Text.Json;
using System.Runtime.InteropServices.WindowsRuntime;
using Luma.Core.Contracts;
using Luma.Core.Gallery;
using Luma.Core.Engine;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.Graphics.Imaging;
namespace Luma.Preview.Services;
internal static class ModelAsset
{
 public static async Task<BitmapImage> Load(string directory,JsonElement asset,CancellationToken token,int decodeSide=640)
 {
  var descriptor=new CachedAsset(asset.GetProperty("resourceId").GetString()!,asset.GetProperty("relativePath").GetString()!);
  byte[] data=await Task.Run(async()=>{
   string path=EngineAssets.Resolve(directory,descriptor);using var file=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.Read,65536,FileOptions.Asynchronous);EngineAssets.VerifyOpenedFile(file,directory);
   if(file.Length is <1 or >16*1024*1024)throw new EngineRpcException("unsupported");byte[] bytes=new byte[(int)file.Length];await file.ReadExactlyAsync(bytes,token);CachePayload.Verify(bytes,asset.GetProperty("bytes").GetInt64(),asset.GetProperty("sha256").GetString()!);return bytes;
  },token);
  using var stream=new MemoryStream(data);using var random=stream.AsRandomAccessStream();var decoder=await BitmapDecoder.CreateAsync(random);
  if(decoder.PixelWidth==0||decoder.PixelHeight==0||decoder.PixelWidth>16384||decoder.PixelHeight>16384||(long)decoder.PixelWidth*decoder.PixelHeight>16_000_000)throw new EngineRpcException("unsupported");
  token.ThrowIfCancellationRequested();random.Seek(0);var image=new BitmapImage();
  if(decoder.PixelWidth>=decoder.PixelHeight)image.DecodePixelWidth=(int)Math.Min((uint)decodeSide,decoder.PixelWidth);else image.DecodePixelHeight=(int)Math.Min((uint)decodeSide,decoder.PixelHeight);
  await image.SetSourceAsync(random);token.ThrowIfCancellationRequested();return image;
 }
}
