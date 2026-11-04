namespace Luma.Core.Viewer;
public static class TextChunks
{
 // Only realized text blocks are shaped. Copy still uses the exact original string.
 public static IReadOnlyList<string> Split(string text){var chunks=new List<string>();for(int offset=0;offset<text.Length;){int count=Math.Min(1024,text.Length-offset);if(offset+count<text.Length){if(char.IsHighSurrogate(text[offset+count-1]))count--;int space=text.LastIndexOfAny([' ','\n','\r'],offset+count-1,count);if(space>=offset+512)count=space-offset+1;}chunks.Add(text.Substring(offset,count));offset+=count;}return chunks;}
}
