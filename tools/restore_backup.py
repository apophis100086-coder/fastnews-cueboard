"""Choose a backup and restore it into this unused, newly extracted project."""
from pathlib import Path
import argparse,json,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from fastnews.storage import restore

def choose_zip():
    import ctypes
    from ctypes import wintypes
    class OpenFile(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('owner',wintypes.HWND),('instance',wintypes.HINSTANCE),
            ('filter',wintypes.LPCWSTR),('custom',wintypes.LPWSTR),('custom_size',wintypes.DWORD),('filter_index',wintypes.DWORD),
            ('file',wintypes.LPWSTR),('max_file',wintypes.DWORD),('file_title',wintypes.LPWSTR),('max_title',wintypes.DWORD),
            ('initial_dir',wintypes.LPCWSTR),('title',wintypes.LPCWSTR),('flags',wintypes.DWORD),
            ('offset',wintypes.WORD),('extension',wintypes.WORD),('default_ext',wintypes.LPCWSTR),
            ('custom_data',ctypes.c_void_p),('hook',ctypes.c_void_p),('template',wintypes.LPCWSTR),
            ('reserved',ctypes.c_void_p),('reserved_size',wintypes.DWORD),('flags_ex',wintypes.DWORD)]
    buffer=ctypes.create_unicode_buffer(32768);item=OpenFile();item.size=ctypes.sizeof(item)
    item.file=ctypes.cast(buffer,wintypes.LPWSTR);item.max_file=len(buffer)
    item.filter='快讯看板数据备份 (*.zip)\0*.zip\0\0';item.title='选择要恢复的看板数据备份'
    item.flags=0x1000|0x80000|0x8
    fn=ctypes.WinDLL('comdlg32').GetOpenFileNameW;fn.argtypes=[ctypes.POINTER(OpenFile)]
    return buffer.value if fn(ctypes.byref(item)) else None

def main():
    p=argparse.ArgumentParser();p.add_argument('archive',nargs='?');p.add_argument('--target',type=Path,default=ROOT);args=p.parse_args()
    archive=args.archive or choose_zip()
    if not archive:print('未选择备份，原数据保留。');return
    print(json.dumps(restore(archive,args.target),ensure_ascii=False,indent=2))
    print('恢复完成。现在可以启动看板；API 密钥需要重新填写。')

if __name__=='__main__':
    try:main()
    except Exception as exc:print(str(exc) if isinstance(exc,ValueError) else '恢复未完成：'+type(exc).__name__);raise SystemExit(1)
