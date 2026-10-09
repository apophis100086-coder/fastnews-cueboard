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
        """No task deadline; individual network operations retain finite limits."""
        self.unbounded=True
        self.deadline=float('inf')
        self.deadline_at=None

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
