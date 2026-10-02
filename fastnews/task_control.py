"""One monotonic deadline for a user-triggered exploration, including cleanup."""
import threading
import time
from datetime import datetime, timedelta, timezone


class TaskStopped(RuntimeError):
    def __init__(self, code):
        self.code=code
        super().__init__('本轮探查已停止，已有结果和选材已保留' if code=='cancelled' else
                         '本轮探查达到时间上限，已有结果和选材已保留')


class TaskControl:
    def __init__(self, seconds=480):
        self.started=time.monotonic()
        self.deadline=self.started+seconds
        self.unbounded=False
        now=datetime.now(timezone.utc)
        self.started_at=now.isoformat()
        self.deadline_at=(now+timedelta(seconds=seconds)).isoformat()
        self.cancelled=threading.Event()

    def make_unbounded(self):
        """智能体直连（文件交换）任务取消运行时硬时限；仅响应用户手动停止。

        智能体通过对话异步回写 response 文件，回写快慢取决于对话节奏，不应被
        分钟级的同步模型时限卡死。做法是把 deadline 推到极远（约 31 年），而不是
        设为 None——因为代码里有多处对 `control.deadline` 做算术（如
        `control.deadline-30`），None 会直接抛 TypeError；推到极远则所有既有算术
        继续可用，check() 永不因超时中断，host_timeout() 返回调用方给出的上限。
        unbounded 标志供 file_runner 识别，从而不设本地等待超时。
        仅用户点"停止"（cancelled）才中断。
        """
        self.unbounded=True
        far=10**9
        self.deadline=time.monotonic()+far
        self.deadline_at=(datetime.now(timezone.utc)+timedelta(seconds=far)).isoformat()

    def check(self):
        if self.cancelled.is_set():
            raise TaskStopped('cancelled')
        if time.monotonic()>=self.deadline:
            raise TaskStopped('timed_out')

    def host_timeout(self, maximum=450, reserve=30):
        self.check()
        remaining=min(maximum,self.deadline-time.monotonic()-reserve)
        if remaining<=0:
            raise TaskStopped('timed_out')
        return remaining
