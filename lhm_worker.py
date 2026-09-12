# -*- coding: utf-8 -*-
"""LHM 隔离工作进程: LibreHardwareMonitor 安装 WinRing0 内核驱动时
原生代码可能 access violation 直接崩进程, 所以必须在子进程里跑。
每 2 秒向 stdout 输出一行 "T <温度>" 或 "T none"。"""
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(BASE, "vendor")
DLL = os.path.join(VENDOR, "LibreHardwareMonitorLib.dll")


def read_temp(comp):
    temps, pkg = [], None
    for hw in comp.Hardware:
        hw.Update()
        for s in hw.Sensors:
            if str(s.SensorType) != "Temperature" or s.Value is None:
                continue
            t = float(s.Value)
            if not (0 < t < 120):
                continue
            if "Package" in (s.Name or ""):
                pkg = t
            temps.append(t)
    if pkg is not None:
        return pkg
    return max(temps) if temps else None


def main():
    import clr
    clr.AddReference(DLL)
    from LibreHardwareMonitor.Hardware import Computer

    c = Computer()
    c.IsCpuEnabled = True
    c.Open()
    print("READY", flush=True)
    while True:
        try:
            t = read_temp(c)
        except Exception:
            t = None
        print(f"T {t if t is not None else 'none'}", flush=True)
        time.sleep(2)


if __name__ == "__main__":
    main()
