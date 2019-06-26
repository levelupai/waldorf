from threading import Semaphore
from time import monotonic as _time


class DSemaphore(Semaphore):
    def __init__(self, value=1):
        super(DSemaphore, self).__init__(value)
        self._max_value = value

    def acquire(self, blocking=True, timeout=None):
        if not blocking and timeout is not None:
            raise ValueError("can't specify timeout for non-blocking acquire")
        rc = False
        endtime = None
        with self._cond:
            while self._value <= 0:
                if not blocking:
                    break
                if timeout is not None:
                    if endtime is None:
                        endtime = _time() + timeout
                    else:
                        timeout = endtime - _time()
                        if timeout <= 0:
                            break
                self._cond.wait(timeout)
            else:
                self._value -= 1
                rc = True
        return rc

    def set_value(self, new_value):
        with self._cond:
            self._value += new_value - self._max_value
            self._cond.notify()
        self._max_value = new_value

    @property
    def value(self):
        return int(self._value)

    @property
    def remain(self):
        return int(self._max_value - self._value)
