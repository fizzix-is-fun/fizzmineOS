"""Fan control per card: driver auto, a fixed %, or hold a target temperature (the gpufans.py loop)."""

MIN_PCT, MAX_PCT, PANIC_C = 30, 100, 85


class FanController:
    def __init__(self, gpus):
        self.gpus = gpus
        self.speed = {}     # card index -> current % while we're driving it
        self.manual = set()  # cards whose fans we've taken over

    def tick(self, settings):
        """settings: card index -> oc dict (fan_mode "auto" | "fixed" | "target", fan_value). Run every few seconds."""
        for i, oc in settings.items():
            mode, value = oc.get("fan_mode") or "auto", oc.get("fan_value")
            if mode == "auto" or value is None:
                if i in self.manual:
                    self.gpus.set_fan(i, None)
                    self.manual.discard(i)
                    self.speed.pop(i, None)
                continue
            if mode == "fixed":
                pct = max(MIN_PCT, min(MAX_PCT, int(value)))
            else:
                t = self.gpus.temp(i)
                pct = self.speed.get(i, 60)
                if t is None:
                    continue
                err = t - int(value)
                if t >= PANIC_C:
                    pct = MAX_PCT
                elif err > 0:
                    pct += min(5, 2 * err)
                elif err < -2:
                    pct -= 2
                pct = max(MIN_PCT, min(MAX_PCT, pct))
            self.speed[i] = pct
            self.manual.add(i)
            self.gpus.set_fan(i, pct)

    def release_all(self):
        for i in list(self.manual):
            self.gpus.set_fan(i, None)
        self.manual.clear()
        self.speed.clear()
