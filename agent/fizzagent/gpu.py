"""Reads per-card stats from NVML. Read-only: nothing here changes clocks or fans."""
import random

try:
    import pynvml as nv
except ImportError:  # lets the agent run in --fake mode on machines without a driver
    nv = None


def _try(fn, *args, default=None):
    try:
        return fn(*args)
    except Exception:
        return default


class NvmlGpus:
    def __init__(self):
        nv.nvmlInit()
        self.handles = [nv.nvmlDeviceGetHandleByIndex(i) for i in range(nv.nvmlDeviceGetCount())]

    def read(self):
        cards = []
        for i, h in enumerate(self.handles):
            mem = _try(nv.nvmlDeviceGetMemoryInfo, h)
            fans = _try(nv.nvmlDeviceGetNumFans, h, default=0) or 0
            fan = [_try(nv.nvmlDeviceGetFanSpeed_v2, h, f) for f in range(fans)]
            power = _try(nv.nvmlDeviceGetPowerUsage, h)
            limit = _try(nv.nvmlDeviceGetEnforcedPowerLimit, h)
            cards.append({
                "index": i,
                "name": _try(nv.nvmlDeviceGetName, h),
                "uuid": _try(nv.nvmlDeviceGetUUID, h),
                "bus_id": _try(lambda: nv.nvmlDeviceGetPciInfo(h).busId),
                "temp_c": _try(nv.nvmlDeviceGetTemperature, h, nv.NVML_TEMPERATURE_GPU),
                "fan_pct": max([f for f in fan if f is not None], default=None),
                "power_w": power / 1000 if power is not None else None,
                "power_limit_w": limit / 1000 if limit is not None else None,
                "core_mhz": _try(nv.nvmlDeviceGetClockInfo, h, nv.NVML_CLOCK_GRAPHICS),
                "mem_mhz": _try(nv.nvmlDeviceGetClockInfo, h, nv.NVML_CLOCK_MEM),
                "util_pct": _try(lambda: nv.nvmlDeviceGetUtilizationRates(h).gpu),
                "vram_used_mb": mem.used // 2**20 if mem else None,
                "vram_total_mb": mem.total // 2**20 if mem else None,
            })
        return cards


class FakeGpus:
    """Two pretend cards, for testing the agent and hub without a GPU."""

    def __init__(self, names=("NVIDIA GeForce RTX 5060 Ti", "NVIDIA GeForce RTX 5060 Ti")):
        self.names = names

    def read(self):
        return [{
            "index": i,
            "name": name,
            "uuid": f"GPU-fake-{i}",
            "bus_id": f"00000000:0{i + 1}:00.0",
            "temp_c": random.randint(58, 72),
            "fan_pct": random.randint(40, 70),
            "power_w": round(random.uniform(90, 100), 1),
            "power_limit_w": 140.0,
            "core_mhz": 2685,
            "mem_mhz": 810,
            "util_pct": 100,
            "vram_used_mb": 1200,
            "vram_total_mb": 16311,
        } for i, name in enumerate(self.names)]
