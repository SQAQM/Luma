namespace Luma.Core.Gallery;
public static class ThumbnailBudget
{
 public static bool Accepts(uint width,uint height,int requested)=>requested is >=64 and <=1200&&width>0&&height>0&&width<=requested&&height<=requested&&(long)width*height*4<=64L*1024*1024;
}
