"""Stop only this workspace's idle server, without touching another port owner."""
import ctypes,json,os,sys
from pathlib import Path
from urllib.request import ProxyHandler,build_opener
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.open_dashboard import check_service,URL

def stop():
    if check_service()!='ready':raise RuntimeError('此项目没有正在运行的看板。没有停止其他程序。')
    record=json.loads((ROOT/'data/dashboard-runtime.json').read_text(encoding='utf-8'))
    state=json.load(build_opener(ProxyHandler({})).open(URL+'api/state',timeout=3))
    if state.get('server_pid')!=record.get('pid'):raise RuntimeError('进程记录与服务不匹配，未停止任何程序。')
    if state.get('active_job'):raise RuntimeError('请先在网页停止当前任务，再关闭看板。')
    if os.name!='nt':raise RuntimeError('此工具仅支持 Windows。')
    from ctypes import wintypes
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];api.OpenProcess.restype=wintypes.HANDLE
    api.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
    api.TerminateProcess.argtypes=[wintypes.HANDLE,wintypes.UINT];api.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=api.OpenProcess(0x1001,False,record['pid'])
    if not handle:raise RuntimeError('无法确认看板进程，未停止任何程序。')
    try:
        buf=ctypes.create_unicode_buffer(32768);size=wintypes.DWORD(len(buf))
        if not api.QueryFullProcessImageNameW(handle,0,buf,ctypes.byref(size)) or Path(buf.value).resolve()!=Path(record['python']).resolve():
            raise RuntimeError('进程身份已变化，未停止任何程序。')
        if Path(record['workspace_id']).resolve()!=ROOT/'data/fastnews.db':raise RuntimeError('工作区不匹配，未停止。')
        # Recheck the server immediately before the explicit user-requested stop.
        if check_service()!='ready':raise RuntimeError('服务状态已变化，请重新检查。')
        if not api.TerminateProcess(handle,0):raise RuntimeError('停止失败，请查看本机进程状态。')
    finally:api.CloseHandle(handle)
    print('本项目看板已停止；数据保留。')

if __name__=='__main__':
    try:stop()
    except Exception as exc:print(str(exc));raise SystemExit(1)
