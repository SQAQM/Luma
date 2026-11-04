using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
using System.Windows.Forms;

public sealed class LumaPatternHost:Form
{
 public bool Warm=true;
 public LumaPatternHost(){FormBorderStyle=FormBorderStyle.None;ShowInTaskbar=false;TopMost=true;StartPosition=FormStartPosition.Manual;Bounds=new Rectangle(-128,-128,1800,1300);Text="Luma synthetic material probe";DoubleBuffered=true;}
 protected override bool ShowWithoutActivation {get{return true;}}
 protected override CreateParams CreateParams {get{var p=base.CreateParams;p.ExStyle|=0x08000000;return p;}}
 protected override void OnPaint(PaintEventArgs e)
 {
  var a=Warm?Color.FromArgb(218,99,69):Color.FromArgb(30,121,185);var b=Warm?Color.FromArgb(115,57,102):Color.FromArgb(25,49,106);
  using(var fill=new LinearGradientBrush(ClientRectangle,a,b,35)){e.Graphics.FillRectangle(fill,ClientRectangle);}
  using(var circle=new SolidBrush(Warm?Color.FromArgb(190,246,177,83):Color.FromArgb(190,69,211,195))){for(int i=0;i<7;i++)e.Graphics.FillEllipse(circle,i*230-80,90+(i%3)*170,330,330);}
  using(var pen=new Pen(Color.FromArgb(70,255,255,255),2)){for(int x=0;x<Width;x+=64)e.Graphics.DrawLine(pen,x,0,x,Height);}
 }
 public void SetWarm(bool warm){Warm=warm;Invalidate();Update();Application.DoEvents();}
}

public static class LumaOwnedCapture
{
 static int activation;
 [StructLayout(LayoutKind.Sequential)]struct Rect{public int L,T,R,B;public Rectangle Box{get{return Rectangle.FromLTRB(L,T,R,B);}}}
 [DllImport("user32.dll")]static extern bool GetWindowRect(IntPtr h,out Rect r);
 [DllImport("user32.dll")]static extern bool IsWindowVisible(IntPtr h);
 [DllImport("user32.dll")]static extern bool IsIconic(IntPtr h);
 [DllImport("user32.dll")]static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")]static extern IntPtr GetTopWindow(IntPtr h);
 [DllImport("user32.dll")]static extern IntPtr GetWindow(IntPtr h,uint kind);
 [DllImport("user32.dll")]static extern uint GetWindowThreadProcessId(IntPtr h,out uint process);
 [StructLayout(LayoutKind.Sequential)]struct HitPoint{public int X,Y;public HitPoint(int x,int y){X=x;Y=y;}}
 [DllImport("user32.dll")]static extern IntPtr WindowFromPoint(HitPoint p);
 [DllImport("user32.dll")]static extern int GetSystemMetrics(int index);
 [DllImport("user32.dll")]static extern void mouse_event(uint flags,uint x,uint y,uint data,UIntPtr info);
 [DllImport("user32.dll")]public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")]public static extern bool SetWindowPos(IntPtr h,IntPtr after,int x,int y,int width,int height,uint flags);
 public static void Place(IntPtr target){SetWindowPos(target,new IntPtr(-1),0,0,1000,650,0x40);SetForegroundWindow(target);}
 static void FocusOwned(IntPtr target,uint process)
 {
  uint actual,foreground;GetWindowThreadProcessId(target,out actual);if(actual!=process)throw new InvalidOperationException("Own activation target required");
  GetWindowThreadProcessId(GetForegroundWindow(),out foreground);if(foreground==process)return;
  SetWindowPos(target,new IntPtr(-1),0,0,0,0,0x13);SetForegroundWindow(target);
  GetWindowThreadProcessId(GetForegroundWindow(),out foreground);if(foreground==process)return;
  Rect r;if(!GetWindowRect(target,out r)||r.Box.Width<700||r.Box.Height<400)throw new InvalidOperationException("Own activation bounds required");
  int x=r.L+260,y=r.T+15;uint hit;GetWindowThreadProcessId(WindowFromPoint(new HitPoint(x,y)),out hit);if(hit!=process)throw new InvalidOperationException("Activation skipped: foreign window");
  mouse_event(0x8001,(uint)Math.Round(x*65535.0/(GetSystemMetrics(0)-1)),(uint)Math.Round(y*65535.0/(GetSystemMetrics(1)-1)),0,UIntPtr.Zero);
  GetWindowThreadProcessId(WindowFromPoint(new HitPoint(x,y)),out hit);if(hit!=process)throw new InvalidOperationException("Activation skipped: foreign pointer target");
  mouse_event(2,0,0,0,UIntPtr.Zero);mouse_event(4,0,0,0,UIntPtr.Zero);Application.DoEvents();
 }
 static Rectangle Verify(IntPtr target,uint process,IntPtr host)
 {
  uint actual,foreground;GetWindowThreadProcessId(target,out actual);GetWindowThreadProcessId(GetForegroundWindow(),out foreground);if(actual!=process||!IsWindowVisible(target)||IsIconic(target)||foreground!=process)throw new InvalidOperationException("Own foreground process required");
  uint owner;GetWindowThreadProcessId(host,out owner);if(owner!=(uint)Environment.ProcessId||!IsWindowVisible(host))throw new InvalidOperationException("Own pattern host required");
  Rect r,b;if(!GetWindowRect(target,out r)||!GetWindowRect(host,out b))throw new InvalidOperationException("Own bounds unavailable");
  var bounds=r.Box;var backdrop=b.Box;backdrop.Inflate(-64,-64);if(!backdrop.Contains(bounds)||bounds.Width<700||bounds.Width>1600||bounds.Height<400||bounds.Height>1000)throw new InvalidOperationException("Synthetic background does not cover capture kernel");
  var kernel=bounds;kernel.Inflate(64,64);
  bool found=false,covered=false;
  for(var h=GetTopWindow(IntPtr.Zero);h!=IntPtr.Zero;h=GetWindow(h,2))
  {
   if(h==target){found=true;continue;}
   if(h==host){if(!found)throw new InvalidOperationException("Pattern host is above target");covered=true;break;}
   if(!IsWindowVisible(h)||IsIconic(h))continue;
   Rect other;if(GetWindowRect(h,out other)&&other.Box.IntersectsWith(kernel)){uint overlay;GetWindowThreadProcessId(h,out overlay);if(overlay!=process)throw new InvalidOperationException(found?"Capture skipped: foreign backdrop":"Capture skipped: foreign overlay");}
  }
  if(!found||!covered)throw new InvalidOperationException("Owned target/host z-order unavailable");
  return bounds;
 }
 public static void Capture(IntPtr target,uint process,IntPtr host,string destination)
 {
  uint a,b;GetWindowThreadProcessId(target,out a);GetWindowThreadProcessId(host,out b);
  if(a!=process||b!=(uint)Environment.ProcessId)throw new InvalidOperationException("Own capture pair required");
  // Keep the generated backdrop immediately behind our target, never sample another app.
  SetWindowPos(host,target,0,0,0,0,0x13);
  FocusOwned(target,process);
  Rect r;if(!GetWindowRect(target,out r))throw new InvalidOperationException("Own title bounds required");
  int x=r.L+260+(activation++%2)*80,y=r.T+15;uint hit;GetWindowThreadProcessId(WindowFromPoint(new HitPoint(x,y)),out hit);
  if(hit!=process)throw new InvalidOperationException("Own title input required");
  mouse_event(0x8001,(uint)Math.Round(x*65535.0/(GetSystemMetrics(0)-1)),(uint)Math.Round(y*65535.0/(GetSystemMetrics(1)-1)),0,UIntPtr.Zero);
  GetWindowThreadProcessId(WindowFromPoint(new HitPoint(x,y)),out hit);if(hit!=process)throw new InvalidOperationException("Own pointer required");
  mouse_event(2,0,0,0,UIntPtr.Zero);mouse_event(4,0,0,0,UIntPtr.Zero);
  var settle=System.Diagnostics.Stopwatch.StartNew();while(settle.ElapsedMilliseconds<180){Application.DoEvents();System.Threading.Thread.Sleep(10);}
  SetWindowPos(host,target,0,0,0,0,0x13);
  var bounds=Verify(target,process,host);
  using(var bitmap=new Bitmap(bounds.Width,bounds.Height,PixelFormat.Format32bppArgb))
  {
   using(var graphics=Graphics.FromImage(bitmap)){graphics.CopyFromScreen(bounds.Location,Point.Empty,bounds.Size,CopyPixelOperation.SourceCopy);}
   if(Verify(target,process,host)!=bounds)throw new InvalidOperationException("Target moved during capture");
   bitmap.Save(destination,ImageFormat.Png);
  }
 }
 public static double Difference(string first,string second,int x,int y,int width,int height)
 {
  using(var a=new Bitmap(first))using(var b=new Bitmap(second))
  {
   double total=0;long n=0;for(int j=y;j<y+height;j++)for(int i=x;i<x+width;i++){var p=a.GetPixel(i,j);var q=b.GetPixel(i,j);total+=Math.Abs(p.R-q.R)+Math.Abs(p.G-q.G)+Math.Abs(p.B-q.B);n+=3;}return total/n;
  }
 }
 public static void Pump(){Application.DoEvents();}
 public static double Mean(string file,int x,int y,int width,int height)
 {
  using(var bitmap=new Bitmap(file)){double total=0;for(int j=y;j<y+height;j++)for(int i=x;i<x+width;i++){var p=bitmap.GetPixel(i,j);total+=.2126*p.R+.7152*p.G+.0722*p.B;}return total/(width*height);}
 }
 public static double TopEdgeDifference(string file)
 {
  using(var b=new Bitmap(file)){double sum=0;for(int x=250;x<750;x++){var a=b.GetPixel(x,0);var c=b.GetPixel(x,8);sum+=Math.Abs(a.R-c.R)+Math.Abs(a.G-c.G)+Math.Abs(a.B-c.B);}return sum/1500;}
 }
}
