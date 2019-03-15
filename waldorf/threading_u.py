from threading import Semaphore


class DSemaphore(Semaphore):
    def __init__(self, value=1):
        super(DSemaphore, self).__init__(value)
        self._max_value = value

    def set_value(self, new_value):
        with self._cond:
            self._value += new_value - self._max_value
            self._cond.notify()
        self._max_value = new_value
