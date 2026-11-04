using System.Security.Cryptography;
using Luma.Core.Engine;
namespace Luma.Core.Gallery;
public static class CachePayload
{
    public static void Verify(byte[] bytes,long length,string checksum)
    {
        if(length is <1 or >4*1024*1024||bytes.LongLength!=length||checksum.Length!=64||!checksum.All(Uri.IsHexDigit)||!CryptographicOperations.FixedTimeEquals(SHA256.HashData(bytes),Convert.FromHexString(checksum)))throw new EngineRpcException("asset_changed");
    }
}
