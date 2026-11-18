using Luma.Core.Design;

internal static class GlassPressMotionTests
{
    public static int Run()
    {
        int count = 0;
        void Check(bool condition, string name)
        {
            if (!condition) throw new Exception("glass motion " + name);
            count++;
        }
        static bool Rest(GlassPressPose pose) => pose == new GlassPressPose(1, 1, 0, 0);

        var motion = new GlassPressMotion();
        Check(motion.TryPressPointer(7, 100, 36, 50, 18, false), "primary pointer starts press");
        var initial = motion.Pose;
        Check(initial.ScaleX is > .95 and < 1 && initial.ScaleY is > .95 and < 1,
            "compact rectangular press gently contracts");
        Check(initial.ShiftX == 0 && initial.ShiftY == 0, "stationary press stays centered");
        Check(!motion.MovePointer(7, 50, 18), "stationary pointer does not restart active springs");
        Check(!motion.TryPressPointer(8, 100, 36, 20, 18, true), "second pointer cannot steal press");
        Check(!motion.MovePointer(8, 500, 200) && motion.Pose == initial,
            "unrelated pointer cannot move press");
        Check(!motion.ReleasePointer(8) && motion.IsPressed, "unrelated pointer cannot release press");
        Check(!motion.MovePointer(7, double.NaN, 18) && motion.Pose == initial,
            "invalid captured position preserves the last valid target");
        Check(motion.MovePointer(7, 110, 18), "held pointer retargets");
        var horizontal = motion.Pose;
        Check(horizontal.ShiftX > 0 && horizontal.ShiftY == 0, "horizontal drag follows pointer");
        Check(horizontal.ScaleX > horizontal.ScaleY, "horizontal drag stretches along travel");
        Check(motion.MovePointer(7, 50, 78), "vertical drag retargets");
        Check(motion.Pose.ScaleY > motion.Pose.ScaleX && motion.Pose.ShiftY > 0,
            "vertical drag stretches along travel");
        Check(motion.ReleasePointer(7) && !motion.IsPressed && Rest(motion.Pose), "release returns resting target");
        var pointerRelease = motion.Spring;
        Check(motion.TryPressPointer(7, 100, 36, 50, 18, false) && motion.Pose == initial,
            "rapid repress uses original bounds without compounding");
        motion.Reset();
        Check(!motion.IsPressed && Rest(motion.Pose) && !motion.MovePointer(7, 90, 30),
            "cancellation clears held pointer and target");

        Check(motion.TryPressPointer(2, 100, 36, 50, 18, true), "touch starts press");
        Check(motion.Pose.ScaleX < initial.ScaleX, "touch has distinct stronger feedback");
        var touchPress = motion.Spring;
        Check(motion.ReleasePointer(2), "touch releases");
        Check(motion.Spring.DampingRatio < pointerRelease.DampingRatio,
            "touch release has softer return than pointer");
        Check(touchPress.Period < motion.Spring.Period, "touch press responds faster than release");

        Check(motion.TryPressKey(GlassPressKey.Space, 100, 36), "keyboard starts centered press");
        Check(motion.Pose == initial, "keyboard uses centered pointer geometry");
        Check(!motion.TryPressKey(GlassPressKey.Space, 100, 36), "key repeat cannot restart press");
        Check(!motion.TryPressPointer(9, 100, 36, 0, 0, false), "pointer cannot steal keyboard press");
        Check(!motion.ReleaseKey(GlassPressKey.Enter) && motion.IsPressed, "other activation key cannot release press");
        Check(motion.ReleaseKey(GlassPressKey.Space) && Rest(motion.Pose), "matching key releases press");
        Check(motion.TryPressKey(GlassPressKey.Enter, 36, 36), "enter starts press");
        motion.Reset();
        Check(!motion.IsPressed && !motion.ReleaseKey(GlassPressKey.Enter) && Rest(motion.Pose),
            "focus policy disable and unload reset keyboard state");

        foreach (var size in new[] { (24d, 24d), (36d, 36d), (84d, 36d), (320d, 36d), (36d, 80d), (1d, 1d) })
        foreach (bool touch in new[] { false, true })
        foreach (var delta in new[] { (0d, 0d), (1d, -1d), (500d, 0d), (0d, -500d), (1e6, -1e6) })
        {
            var pose = GlassPressMotion.Calculate(size.Item1, size.Item2, delta.Item1, delta.Item2, touch);
            Check(pose.ScaleX is >= .94 and <= 1 && pose.ScaleY is >= .94 and <= 1,
                "bounded deformation for narrow wide and tall controls");
            Check(Math.Abs(pose.ShiftX) <= 1.25 && Math.Abs(pose.ShiftY) <= 1.25,
                "drag translation remains gentle");
            Check(Math.Abs(pose.ShiftX) + size.Item1 * pose.ScaleX / 2 <= size.Item1 / 2 + 1e-9 &&
                  Math.Abs(pose.ShiftY) + size.Item2 * pose.ScaleY / 2 <= size.Item2 / 2 + 1e-9,
                "transformed bounds cannot overlap adjacent controls");
        }

        foreach (var invalid in new[] { 0d, -1d, double.NaN, double.PositiveInfinity })
        {
            Check(Rest(GlassPressMotion.Calculate(invalid, 36, 1, 1, false)), "invalid layout has resting pose");
            Check(!motion.TryPressPointer(1, 100, invalid, 0, 0, false) && !motion.IsPressed,
                "invalid layout cannot latch input");
        }
        Check(Rest(GlassPressMotion.Calculate(100, 36, double.NaN, 0, false)), "invalid coordinates do not reach compositor");
        return count;
    }
}
