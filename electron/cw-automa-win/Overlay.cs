// Overlay.cs — virtual cursor + status HUD (mirrors macOS VirtualCursor.swift +
// StatusHUD.swift). Runs on a dedicated STA thread with its own message loop;
// commands from the stdio thread are marshalled onto the UI thread.
//
// Both windows are topmost, tool-window, click-through, and never take focus,
// so the agent's pointer is purely visual and the user's real input is free.
//
// HUD lifecycle (matches macOS StatusHUD): the pill is HIDDEN until the agent
// actually does something (cursor move / action pulse / explicit hud_show), then
// auto-hides after a short idle so it never lingers while CoWorker is idle.
// Pausing switches it to a persistent red pill.

using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Text;
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
                // Show then hide the cursor so its handle exists (used to marshal
                // commands); force the HUD handle without ever showing it, so the
                // pill stays invisible until the agent acts.
                _cursor.Show();
                _cursor.Hide();
                _ = _hud.Handle;
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
        try { c.BeginInvoke((Action)(() => { try { action(); } catch { /* never fault the UI loop */ } })); } catch { /* shutting down */ }
    }

    public static void Move(int x, int y) => OnUi(() => { _cursor.MoveTo(x, y); _hud.ShowActive(); });
    public static void ShowCursor() => OnUi(() => _cursor.ShowCursor());
    public static void Hide() => OnUi(() => _cursor.HideCursor());
    public static void Pulse() => OnUi(() => { _cursor.Pulse(); _hud.ShowActive(); });
    public static void ClickFx() => OnUi(() => { _cursor.Pulse(); _hud.ShowActive(); });

    public static void HudShow() => OnUi(() => _hud.ShowActive());
    public static void HudHide() => OnUi(() => _hud.HideNow());
    public static void HudPause(bool paused) => OnUi(() => { if (paused) _hud.ShowPaused(); else _hud.ShowActive(); });
    public static void SetLabel(string label) => OnUi(() => _hud.SetLabel(label ?? ""));

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
        private const int PillHeight = 38;
        private const int PadX = 15;
        private const int DotD = 9;
        private const int DotTitleGap = 11;
        private const int TitleHintGap = 12;
        private const int BottomMargin = 18;
        private const int AutoHideMs = 4000;

        private readonly System.Windows.Forms.Timer _hideTimer;
        private readonly Font _titleFont = new Font("Segoe UI", 9.75f, FontStyle.Bold);
        private readonly Font _hintFont = new Font("Segoe UI", 9f, FontStyle.Regular);

        private bool _paused;
        private bool _shown;
        private string _stopLabel = "Ctrl + Alt + Shift + Esc";

        public HudForm()
        {
            AutoScaleMode = AutoScaleMode.None;
            Height = PillHeight;
            Width = 300;
            _hideTimer = new System.Windows.Forms.Timer { Interval = AutoHideMs };
            _hideTimer.Tick += (s, e) => { _hideTimer.Stop(); HideNow(); };
        }

        public void SetLabel(string label)
        {
            if (!string.IsNullOrEmpty(label)) _stopLabel = label;
            if (_shown) { Render(false); Invalidate(); }
        }

        public void ShowActive()
        {
            _paused = false;
            Render(true);
            _hideTimer.Stop();
            _hideTimer.Start();
        }

        public void ShowPaused()
        {
            _paused = true;
            _hideTimer.Stop();
            Render(true);
        }

        public void HideNow()
        {
            _hideTimer.Stop();
            _shown = false;
            Visible = false;
        }

        private string Title() => _paused ? "CoWorker is paused" : "CoWorker is controlling";
        private string Hint() => _paused ? $"{_stopLabel} to resume" : $"{_stopLabel} to pause";

        private void Render(bool show)
        {
            using var g = CreateGraphics();
            var titleSize = g.MeasureString(Title(), _titleFont);
            var hintSize = g.MeasureString(Hint(), _hintFont);
            int width = PadX + DotD + DotTitleGap + (int)Math.Ceiling(titleSize.Width)
                        + TitleHintGap + (int)Math.Ceiling(hintSize.Width) + PadX;

            var wa = Screen.PrimaryScreen?.WorkingArea ?? new Rectangle(0, 0, 1280, 720);
            int x = wa.Left + (wa.Width - width) / 2;
            int y = wa.Bottom - PillHeight - BottomMargin;
            SetBounds(x, y, width, PillHeight);

            // Clip the window to the pill so the rounded corners are crisp (no
            // transparency-key fringing around the antialiased edge).
            using var path = Rounded(new Rectangle(0, 0, width, PillHeight), PillHeight / 2);
            var old = Region;
            Region = new Region(path);
            old?.Dispose();

            _shown = true;
            if (show && !Visible) Visible = true;
            Invalidate();
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            if (!_shown) return;

            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.TextRenderingHint = TextRenderingHint.ClearTypeGridFit;
            var rect = new Rectangle(0, 0, Width, Height);

            Color surface = _paused ? Color.FromArgb(255, 34, 18, 20) : Color.FromArgb(255, 20, 24, 33);
            Color border = _paused ? Color.FromArgb(255, 198, 74, 74) : Color.FromArgb(255, 64, 120, 220);
            Color dot = _paused ? Color.FromArgb(255, 255, 116, 116) : Color.FromArgb(255, 96, 158, 255);
            Color titleColor = _paused ? Color.FromArgb(255, 255, 226, 226) : Color.FromArgb(255, 238, 243, 255);
            Color hintColor = _paused ? Color.FromArgb(255, 206, 158, 158) : Color.FromArgb(255, 148, 162, 190);

            using (var path = Rounded(rect, Height / 2))
            using (var brush = new SolidBrush(surface))
                g.FillPath(brush, path);
            using (var path = Rounded(rect, Height / 2))
            using (var pen = new Pen(border, 1.4f))
                g.DrawPath(pen, path);

            // Status indicator: a soft halo behind a solid dot.
            int dcx = PadX + DotD / 2;
            int dcy = Height / 2;
            using (var halo = new SolidBrush(_paused ? Color.FromArgb(255, 60, 34, 34) : Color.FromArgb(255, 30, 42, 66)))
                g.FillEllipse(halo, dcx - DotD / 2 - 3, dcy - DotD / 2 - 3, DotD + 6, DotD + 6);
            using (var b = new SolidBrush(dot))
                g.FillEllipse(b, dcx - DotD / 2, dcy - DotD / 2, DotD, DotD);

            string title = Title();
            string hint = Hint();
            var titleSize = g.MeasureString(title, _titleFont);
            var hintSize = g.MeasureString(hint, _hintFont);
            float tx = PadX + DotD + DotTitleGap;
            float ty = (Height - titleSize.Height) / 2f;
            using (var tb = new SolidBrush(titleColor))
                g.DrawString(title, _titleFont, tb, tx, ty);
            float hx = tx + titleSize.Width + TitleHintGap;
            float hy = (Height - hintSize.Height) / 2f;
            using (var hb = new SolidBrush(hintColor))
                g.DrawString(hint, _hintFont, hb, hx, hy);
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
