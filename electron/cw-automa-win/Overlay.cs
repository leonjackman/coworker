// Overlay.cs — virtual cursor + status HUD (mirrors macOS VirtualCursor.swift +
// StatusHUD.swift). Runs on a dedicated STA thread with its own message loop;
// commands from the stdio thread are marshalled onto the UI thread.
//
// Both windows are topmost, tool-window, click-through, and never take focus,
// so the agent's pointer is purely visual and the user's real input is free.

using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

namespace CwAutomaWin;

internal static class Overlay
{
    private static CursorForm _cursor;
    private static HudForm _hud;
    private static volatile bool _started;

    public static void Start()
    {
        if (_started) return;
        var thread = new Thread(() =>
        {
            try
            {
                _cursor = new CursorForm();
                _hud = new HudForm();
                _cursor.Show();
                _hud.Show();
                _cursor.Hide();
                _started = true;
                Application.Run(new ApplicationContext());
            }
            catch { _started = false; }
        });
        thread.IsBackground = true;
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
    }

    private static void OnUi(Action action)
    {
        var c = _cursor;
        if (c == null || c.IsDisposed) return;
        try { c.BeginInvoke(action); } catch { /* shutting down */ }
    }

    public static void Move(int x, int y) => OnUi(() => _cursor.MoveTo(x, y));
    public static void ShowCursor() => OnUi(() => _cursor.ShowCursor());
    public static void Hide() => OnUi(() => _cursor.HideCursor());
    public static void Pulse() => OnUi(() => _cursor.Pulse());
    public static void ClickFx() => OnUi(() => _cursor.Pulse());

    public static void HudShow() => OnUi(() => { _hud.Show(); _hud.Refresh(); });
    public static void HudHide() => OnUi(() => _hud.Hide());
    public static void HudPause(bool paused) => OnUi(() => { _hud.Paused = paused; _hud.Invalidate(); });
    public static void SetLabel(string label) => OnUi(() => { _hud.StopLabel = label ?? ""; _hud.Invalidate(); });

    public static Point Current()
    {
        var c = _cursor;
        if (c == null) return Point.Empty;
        try { return c.Position; } catch { return Point.Empty; }
    }

    private abstract class OverlayForm : Form
    {
        protected const int WS_EX_TRANSPARENT = 0x20;
        protected const int WS_EX_LAYERED = 0x80000;
        protected const int WS_EX_NOACTIVATE = 0x08000000;
        protected const int WS_EX_TOOLWINDOW = 0x80;

        protected OverlayForm()
        {
            FormBorderStyle = FormBorderStyle.None;
            StartPosition = FormStartPosition.Manual;
            ShowInTaskbar = false;
            TopMost = true;
            BackColor = Color.Magenta;
            TransparencyKey = Color.Magenta;
        }

        protected override bool ShowWithoutActivation => true;

        protected override CreateParams CreateParams
        {
            get
            {
                var cp = base.CreateParams;
                cp.ExStyle |= WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW;
                return cp;
            }
        }
    }

    private sealed class CursorForm : OverlayForm
    {
        private bool _visible;
        private Color _ring = Color.White;

        public CursorForm()
        {
            Width = 22;
            Height = 22;
        }

        public Point Position => new(Left + Width / 2, Top + Height / 2);

        public void MoveTo(int x, int y)
        {
            Left = x - Width / 2;
            Top = y - Height / 2;
            _visible = true;
            Visible = true;
            BringToFront();
            Invalidate();
        }

        public void ShowCursor() { _visible = true; Visible = true; Invalidate(); }
        public void HideCursor() { _visible = false; Visible = false; }

        public void Pulse()
        {
            _ring = Color.FromArgb(120, 200, 255);
            Invalidate();
            var t = new System.Windows.Forms.Timer { Interval = 180 };
            t.Tick += (s, e) => { t.Stop(); t.Dispose(); _ring = Color.White; if (!IsDisposed) Invalidate(); };
            t.Start();
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            if (!_visible) return;
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            using var fill = new SolidBrush(Color.FromArgb(220, 30, 144, 255));
            using var ring = new Pen(_ring, 2);
            e.Graphics.FillEllipse(fill, 3, 3, Width - 6, Height - 6);
            e.Graphics.DrawEllipse(ring, 3, 3, Width - 6, Height - 6);
        }
    }

    private sealed class HudForm : OverlayForm
    {
        public bool Paused;
        public string StopLabel = "";

        public HudForm()
        {
            Width = 210;
            Height = 34;
            var wa = Screen.PrimaryScreen?.WorkingArea ?? new Rectangle(0, 0, 1280, 720);
            Left = wa.Left + wa.Width / 2 - Width / 2;
            Top = wa.Bottom - Height - 12;
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            var rect = new Rectangle(0, 0, Width - 1, Height - 1);
            using (var bg = new SolidBrush(Color.FromArgb(230, 20, 20, 24)))
            using (var path = Rounded(rect, 12))
                e.Graphics.FillPath(bg, path);
            using (var pen = new Pen(Paused ? Color.FromArgb(255, 170, 60) : Color.FromArgb(60, 200, 120), 1.5f))
            using (var path = Rounded(rect, 12))
                e.Graphics.DrawPath(pen, path);

            string text = Paused ? "CoWorker paused" : "CoWorker controlling";
            if (!string.IsNullOrEmpty(StopLabel)) text += $"  ({StopLabel} to stop)";
            using var fg = new SolidBrush(Color.White);
            using var font = new Font("Segoe UI", 9f, FontStyle.Regular);
            var fmt = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center };
            e.Graphics.DrawString(text, font, fg, new RectangleF(0, 0, Width, Height), fmt);
        }

        private static GraphicsPath Rounded(Rectangle r, int radius)
        {
            int d = radius * 2;
            var path = new GraphicsPath();
            path.AddArc(r.X, r.Y, d, d, 180, 90);
            path.AddArc(r.Right - d, r.Y, d, d, 270, 90);
            path.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
            path.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
            path.CloseFigure();
            return path;
        }
    }
}
