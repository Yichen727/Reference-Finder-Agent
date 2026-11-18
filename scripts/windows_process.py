"""Windows process handles and jobs; no process-name-wide termination."""

import ctypes
import subprocess
from ctypes import wintypes as w


class BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64),
        ("job_time", ctypes.c_int64),
        ("flags", w.DWORD),
        ("min_working_set", ctypes.c_size_t),
        ("max_working_set", ctypes.c_size_t),
        ("active_processes", w.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority", w.DWORD),
        ("scheduling", w.DWORD),
    ]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", BasicLimits),
        ("io", ctypes.c_uint64 * 6),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


class ThreadEntry(ctypes.Structure):
    _fields_ = [
        ("size", w.DWORD),
        ("usage", w.DWORD),
        ("thread_id", w.DWORD),
        ("owner_pid", w.DWORD),
        ("base_priority", w.LONG),
        ("delta_priority", w.LONG),
        ("flags", w.DWORD),
    ]


class WindowsAPI:
    def __init__(self):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "CloseHandle": ([w.HANDLE], w.BOOL),
            "GetProcessTimes": ([w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4, w.BOOL),
            "GetExitCodeProcess": ([w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
            "QueryFullProcessImageNameW": ([w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)], w.BOOL),
            "TerminateProcess": ([w.HANDLE, w.UINT], w.BOOL),
            "CreateJobObjectW": ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "CreateToolhelp32Snapshot": ([w.DWORD, w.DWORD], w.HANDLE),
            "Thread32First": ([w.HANDLE, ctypes.POINTER(ThreadEntry)], w.BOOL),
            "Thread32Next": ([w.HANDLE, ctypes.POINTER(ThreadEntry)], w.BOOL),
            "OpenThread": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "ResumeThread": ([w.HANDLE], w.DWORD),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result

    def opened(self, pid, terminate=False):
        handle = self.kernel.OpenProcess(0x1000 | (1 if terminate else 0), False, pid)
        if not handle and ctypes.get_last_error() != 87:
            raise OSError(f"Cannot inspect process {pid}; check process permissions.")
        return handle

    def matches(self, handle, identity):
        times = [w.FILETIME() for _ in range(4)]
        if not self.kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            raise ctypes.WinError(ctypes.get_last_error())
        created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        image, size = ctypes.create_unicode_buffer(32768), w.DWORD(32768)
        if not self.kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
            if not self.active(handle):
                return False
            raise ctypes.WinError(ctypes.get_last_error())
        # CIM timestamps have microsecond precision, unlike native FILETIME (100ns).
        return created // 10 == int(identity["created"]) // 10 and (
            image.value.casefold() == identity["exe"].casefold()
        )

    def active(self, handle):
        code = w.DWORD()
        if not self.kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value == 259  # STILL_ACTIVE

    def running(self, identity):
        handle = self.opened(identity["pid"])
        if not handle:
            return False
        try:
            return self.active(handle) and self.matches(handle, identity)
        finally:
            self.kernel.CloseHandle(handle)

    def stop(self, identity):
        handle = self.opened(identity["pid"], terminate=True)
        if not handle:
            return
        try:
            if not self.active(handle):
                return
            # Hold a handle across validation and termination to prevent PID reuse.
            if not self.matches(handle, identity):
                raise RuntimeError(f"Process {identity['pid']} changed identity; it was left running.")
            if not self.kernel.TerminateProcess(handle, 1) and self.active(handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self.kernel.CloseHandle(handle)


class WindowsJob:
    """Only service processes join this job; the launcher and browser never do."""

    def __init__(self):
        self.api = WindowsAPI()
        self.handle = self.api.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.kernel.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            self.close()
            raise RuntimeError("Could not configure Windows process-tree cleanup.")
        self.callback = ctypes.WINFUNCTYPE(w.BOOL, w.DWORD)(self.console_event)
        self.api.kernel.SetConsoleCtrlHandler.argtypes = [type(self.callback), w.BOOL]
        self.api.kernel.SetConsoleCtrlHandler.restype = w.BOOL
        if not self.api.kernel.SetConsoleCtrlHandler(self.callback, True):
            self.close()
            raise RuntimeError("Could not register terminal-close cleanup.")

    def console_event(self, event):
        if event in {2, 5, 6}:  # Console close, logoff, OS shutdown.
            self.close()
            return True
        return False  # Preserve Python's Ctrl+C handling.

    def spawn(self, command, **kwargs):
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        process = subprocess.Popen(
            command, creationflags=4 | subprocess.CREATE_NEW_PROCESS_GROUP, startupinfo=startup, **kwargs
        )
        try:
            handle = self.api.kernel.OpenProcess(0x0100 | 0x0001, False, process.pid)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if not self.api.kernel.AssignProcessToJobObject(self.handle, handle):
                    raise RuntimeError("Could not attach the service to its Windows cleanup job.")
            finally:
                self.api.kernel.CloseHandle(handle)
            # The service cannot create children or bind a port before joining the job.
            self.resume(process.pid)
            return process
        except BaseException:
            process.kill()
            process.wait(timeout=5)
            raise

    def resume(self, pid):
        snapshot = self.api.kernel.CreateToolhelp32Snapshot(4, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            more = self.api.kernel.Thread32First(snapshot, ctypes.byref(entry))
            while more:
                if entry.owner_pid == pid:
                    thread = self.api.kernel.OpenThread(2, False, entry.thread_id)
                    if not thread:
                        raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        if self.api.kernel.ResumeThread(thread) == 0xFFFFFFFF:
                            raise ctypes.WinError(ctypes.get_last_error())
                    finally:
                        self.api.kernel.CloseHandle(thread)
                    return
                more = self.api.kernel.Thread32Next(snapshot, ctypes.byref(entry))
            raise RuntimeError("Could not resume the newly launched service.")
        finally:
            self.api.kernel.CloseHandle(snapshot)

    def close(self):
        handle, self.handle = getattr(self, "handle", None), None
        if handle:
            self.api.kernel.CloseHandle(handle)
        if getattr(self, "callback", None):
            self.api.kernel.SetConsoleCtrlHandler(self.callback, False)
