namespace Luma.Core.Viewer;
public sealed class Viewport
{
 public double Width{get;private set;}=1;public double Height{get;private set;}=1;
 public double ImageWidth{get;private set;}=1;public double ImageHeight{get;private set;}=1;
 public double Scale{get;private set;}=1;public double X{get;private set;}public double Y{get;private set;}
 public int Turns{get;private set;}public bool IsFit{get;private set;}=true;
 private double RotatedWidth=>Turns%2==0?ImageWidth:ImageHeight;
 private double RotatedHeight=>Turns%2==0?ImageHeight:ImageWidth;
 public void SetImage(double width,double height){ImageWidth=Math.Max(1,width);ImageHeight=Math.Max(1,height);Turns=0;Fit();}
 public void Resize(double width,double height){Width=Math.Max(1,width);Height=Math.Max(1,height);if(IsFit)Fit();else Clamp();}
 public void Fit(){IsFit=true;Scale=Math.Clamp(Math.Min(Width/RotatedWidth,Height/RotatedHeight),.0001,32);X=Y=0;}
 public void Actual(double rasterScale){IsFit=false;Scale=1/Math.Max(1,rasterScale);X=Y=0;Clamp();}
 public void ZoomAt(double factor,double x,double y){if(!double.IsFinite(factor)||factor<=0)return;IsFit=false;double value=Math.Clamp(Scale*factor,.0001,32),ratio=value/Scale;X=(x-Width/2)-(x-Width/2-X)*ratio;Y=(y-Height/2)-(y-Height/2-Y)*ratio;Scale=value;Clamp();}
 public void Pan(double x,double y){if(!double.IsFinite(x)||!double.IsFinite(y))return;X+=x;Y+=y;Clamp();}
 public void Rotate(){Turns=(Turns+1)%4;if(IsFit)Fit();else Clamp();}
 private void Clamp(){X=Math.Clamp(X,-Math.Max(0,(RotatedWidth*Scale-Width)/2),Math.Max(0,(RotatedWidth*Scale-Width)/2));Y=Math.Clamp(Y,-Math.Max(0,(RotatedHeight*Scale-Height)/2),Math.Max(0,(RotatedHeight*Scale-Height)/2));}
}
