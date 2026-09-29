// Explicit Unicode Windows shell API: WScript.Shell can corrupt Chinese paths
// under some system locales. No registry, process, or network changes here.
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Text;

namespace Fastnews {
    public class ShortcutInfo {
        public string TargetPath;
        public string Arguments;
        public string WorkingDirectory;
        public string Description;
    }

    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
    internal class ShellLink { }

    [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown),
     Guid("000214F9-0000-0000-C000-000000000046")]
    internal interface IShellLinkW {
        void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path, int count, IntPtr findData, int flags);
        void GetIDList(out IntPtr idList);
        void SetIDList(IntPtr idList);
        void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder text, int count);
        void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string text);
        void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path, int count);
        void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string path);
        void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder text, int count);
        void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string text);
        void GetHotkey(out short hotkey);
        void SetHotkey(short hotkey);
        void GetShowCmd(out int command);
        void SetShowCmd(int command);
        void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path, int count, out int index);
        void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string path, int index);
        void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string path, int reserved);
        void Resolve(IntPtr window, int flags);
        void SetPath([MarshalAs(UnmanagedType.LPWStr)] string path);
    }

    public static class WindowsShortcut {
        public static void Save(string file, string target, string arguments, string directory, string description) {
            var link = (IShellLinkW)new ShellLink();
            try {
                link.SetPath(target);
                link.SetArguments(arguments);
                link.SetWorkingDirectory(directory);
                link.SetDescription(description);
                link.SetShowCmd(7);
                ((IPersistFile)link).Save(file, true);
            } finally { Marshal.FinalReleaseComObject(link); }
        }

        public static ShortcutInfo Read(string file) {
            var link = (IShellLinkW)new ShellLink();
            try {
                ((IPersistFile)link).Load(file, 0);
                var target = new StringBuilder(32768);
                var args = new StringBuilder(32768);
                var directory = new StringBuilder(32768);
                var description = new StringBuilder(4096);
                link.GetPath(target, target.Capacity, IntPtr.Zero, 4);
                link.GetArguments(args, args.Capacity);
                link.GetWorkingDirectory(directory, directory.Capacity);
                link.GetDescription(description, description.Capacity);
                return new ShortcutInfo { TargetPath = target.ToString(), Arguments = args.ToString(),
                    WorkingDirectory = directory.ToString(), Description = description.ToString() };
            } finally { Marshal.FinalReleaseComObject(link); }
        }
    }
}
