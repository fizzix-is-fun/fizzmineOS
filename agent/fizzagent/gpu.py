"""Per-card stats and overclock settings through NVML (the agent runs as root to set them)."""
import random
import re
import subprocess

try:
    import pynvml as nv
except ImportError:  # lets the agent run in --fake mode on machines without a driver
    nv = None


def _try(fn, *args, default=None):
    try:
        return fn(*args)
    except Exception:
        return default


def short_brand(svendor):
    """'Micro-Star International Co., Ltd. [MSI]' -> 'MSI', 'ASUSTeK Computer Inc.' -> 'ASUS'."""
    if not svendor:
        return None
    m = re.search(r"\[([^\]]+)\]", svendor)
    if m:
        return m.group(1)
    name = re.sub(r"\(.*?\)", "", svendor.split(",")[0])
    name = re.sub(r"\b(Inc|Corp|Corporation|Co|Ltd|Limited|Technology|Technologies|International|Computer|"
                  r"Microsystems|Electronics)\b\.?", "", name)
    name = " ".join(name.split())
    return {"ASUSTeK": "ASUS"}.get(name, name) or None


def card_brand(bus_id):
    """The board maker (MSI, ASUS, Gigabyte...) from the PCI subsystem vendor, via lspci's name list."""
    try:
        out = subprocess.run(["lspci", "-vmm", "-s", bus_id[-12:]], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError, TypeError):
        return None
    m = re.search(r"^SVendor:\s*(.+)$", out, re.M)
    return short_brand(m.group(1)) if m else None


class NvmlGpus:
    def __init__(self):
        self.handles, self.fixed, self.error = [], [], None
        self.connect()

    def connect(self):
        """Without a loaded NVIDIA driver (mid driver install, say) the rig reports no cards and tries again later."""
        try:
            nv.nvmlInit()
            self.handles = [nv.nvmlDeviceGetHandleByIndex(i) for i in range(nv.nvmlDeviceGetCount())]
        except nv.NVMLError as e:
            self.error = f"NVIDIA driver not loaded ({e})"
            return
        self.error = None
        # things that don't change while the agent runs
        self.fixed = [{
            "brand": card_brand(_try(lambda: nv.nvmlDeviceGetPciInfo(h).busId)),
            "vbios": _try(nv.nvmlDeviceGetVbiosVersion, h),
        } for h in self.handles]

    def read(self):
        if self.error:
            self.connect()
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
                **self.fixed[i],
                "uuid": _try(nv.nvmlDeviceGetUUID, h),
                "bus_id": _try(lambda: nv.nvmlDeviceGetPciInfo(h).busId),
                "temp_c": _try(nv.nvmlDeviceGetTemperature, h, nv.NVML_TEMPERATURE_GPU),
                "fan_pct": max([f for f in fan if f is not None], default=None),
                "power_w": power / 1000 if power is not None else None,
                "power_limit_w": limit / 1000 if limit is not None else None,
                "core_mhz": _try(nv.nvmlDeviceGetClockInfo, h, nv.NVML_CLOCK_GRAPHICS),
                "mem_mhz": _try(nv.nvmlDeviceGetClockInfo, h, nv.NVML_CLOCK_MEM),
                "core_offset_mhz": _try(nv.nvmlDeviceGetGpcClkVfOffset, h),
                "mem_offset_mhz": _try(nv.nvmlDeviceGetMemClkVfOffset, h),
                "util_pct": _try(lambda: nv.nvmlDeviceGetUtilizationRates(h).gpu),
                "vram_used_mb": mem.used // 2**20 if mem else None,
                "vram_total_mb": mem.total // 2**20 if mem else None,
            })
        return cards

    # --- overclocking. Each field set to a number applies it; None puts that setting back to stock.

    def apply_oc(self, i, oc):
        h, errors = self.handles[i], []

        def do(field, fn):
            try:
                fn()
            except Exception as e:
                errors.append(f"{field}: {e}")

        pl = oc.get("power_w")

        def set_power():
            if not pl:
                return nv.nvmlDeviceSetPowerManagementLimit(h, nv.nvmlDeviceGetPowerManagementDefaultLimit(h))
            # cards refuse limits outside their range (a 5060 Ti won't go to 140 W): use the nearest one and say so
            lo, hi = _try(nv.nvmlDeviceGetPowerManagementLimitConstraints, h, default=(0, 10**9))
            mw = max(lo, min(hi, int(pl * 1000)))
            nv.nvmlDeviceSetPowerManagementLimit(h, mw)
            if mw != int(pl * 1000):
                errors.append(f"power: {pl} W is outside this card's {lo / 1000:g}-{hi / 1000:g} W, set {mw / 1000:g} W")

        do("power", set_power)
        core = oc.get("core_lock_mhz")
        do("core lock", lambda: nv.nvmlDeviceSetGpuLockedClocks(h, core, core) if core else nv.nvmlDeviceResetGpuLockedClocks(h))
        mem = oc.get("mem_lock_mhz")
        do("mem lock", lambda: nv.nvmlDeviceSetMemoryLockedClocks(h, mem, mem) if mem else nv.nvmlDeviceResetMemoryLockedClocks(h))
        do("core offset", lambda: nv.nvmlDeviceSetGpcClkVfOffset(h, int(oc.get("core_offset_mhz") or 0)))
        do("mem offset", lambda: nv.nvmlDeviceSetMemClkVfOffset(h, int(oc.get("mem_offset_mhz") or 0)))
        return errors

    def temp(self, i):
        return _try(nv.nvmlDeviceGetTemperature, self.handles[i], nv.NVML_TEMPERATURE_GPU)

    def set_fan(self, i, pct):
        """pct=None hands the fans back to the driver's automatic curve."""
        h = self.handles[i]
        for f in range(_try(nv.nvmlDeviceGetNumFans, h, default=0) or 0):
            if pct is None:
                _try(nv.nvmlDeviceSetDefaultFanSpeed_v2, h, f)
            else:
                _try(nv.nvmlDeviceSetFanSpeed_v2, h, f, int(pct))


class FakeGpus:
    """Two pretend cards, for testing the agent and hub without a GPU."""

    def __init__(self, names=("NVIDIA GeForce RTX 5060 Ti", "NVIDIA GeForce RTX 5060 Ti")):
        self.names = names
        self.oc = [{} for _ in names]
        self.fan = [None for _ in names]

    def apply_oc(self, i, oc):
        self.oc[i] = dict(oc)
        return []

    def temp(self, i):
        return 66

    def set_fan(self, i, pct):
        self.fan[i] = pct

    def read(self):
        return [{
            "index": i,
            "name": name,
            "brand": ("MSI", "Gigabyte")[i % 2],
            "vbios": "98.06.1F.00.9A",
            "uuid": f"GPU-fake-{i}",
            "bus_id": f"00000000:0{i + 1}:00.0",
            "temp_c": random.randint(58, 72),
            "fan_pct": self.fan[i] if self.fan[i] is not None else random.randint(40, 70),
            "power_w": round(random.uniform(90, 100), 1),
            "power_limit_w": float(self.oc[i].get("power_w") or 180),
            "core_mhz": self.oc[i].get("core_lock_mhz") or 2685,
            "mem_mhz": self.oc[i].get("mem_lock_mhz") or 14001,
            "core_offset_mhz": self.oc[i].get("core_offset_mhz") or 0,
            "mem_offset_mhz": self.oc[i].get("mem_offset_mhz") or 0,
            "util_pct": 100,
            "vram_used_mb": 1200,
            "vram_total_mb": 16311,
        } for i, name in enumerate(self.names)]
