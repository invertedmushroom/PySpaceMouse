import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

# ===== User-configurable settings =====
# Axis inversion flags
INVERT_X = False         # left/right
INVERT_Y = True          # forward/back
INVERT_Z = True          # zoom
INVERT_YAW = True        # twist/rotate direction

# Zoom direction
SWAP_Y_Z = False

# Movement sensitivity (translation) – pulsed
MOVE_PRESS_MS = 0.020 # seconds key is held per pulse
MOVE_MIN_HZ = 15.0
MOVE_MAX_HZ = 30.0
MOVE_DEADZONE = 0.001
MOVE_HOLD_THRESHOLD = 0.40
MOVE_EMA_ALPHA = 0.3 # smoothing for axis

# Zoom sensitivity
ZOOM_PRESS_MS = 0.010
ZOOM_MIN_HZ = 8.0
ZOOM_MAX_HZ = 18.0
ZOOM_DEADZONE = 0.001
ZOOM_HOLD_THRESHOLD = 0.5    # 1.0 disables hold behavior in pulse mode
ZOOM_EMA_ALPHA = 0.3
# ===== End user-configurable settings =====

# Use the library in this package
from pyspacemouse import open as sm_open, read as sm_read, close as sm_close

try:
	import pynput.keyboard as keyboard
except ImportError as e:
	raise SystemExit(
		"Missing dependency: pynput. Install it with 'pip install pynput'"
	) from e


def clamp(v: float, lo: float, hi: float) -> float:
	return max(lo, min(hi, v))


@dataclass
class PulseState:
	key: Any
	mode: str = "pulse"  # 'pulse' or 'hold'
	pressed: bool = False
	held: bool = False
	last_pulse_time: float = 0.0
	release_due: float = 0.0


class InterpolatedKeyController:
	"""Convert analog axis magnitudes into keyboard pulses with speed control.

	Strategy:
	- Below deadzone: ensure key is released.
	- Between deadzone..hold_threshold: generate short key pulses with a frequency
	  proportional to the axis magnitude (duty-cycle control).
	- Above hold_threshold: hold the key down continuously (smoother at high speeds).
	"""

	def __init__(
		self,
		kb: keyboard.Controller,
		press_ms: float = 0.02,
		min_hz: float = 3.0,
		max_hz: float = 25.0,
		deadzone: float = 0.05,
		hold_threshold: float = 0.9,
		ema_alpha: float = 0.25,
	) -> None:
		self.kb = kb
		self.press_ms = press_ms
		self.min_hz = min_hz
		self.max_hz = max_hz
		self.deadzone = deadzone
		self.hold_threshold = hold_threshold
		self.ema_alpha = ema_alpha
		self.states: Dict[str, PulseState] = {}
		self.filtered: Dict[str, float] = {}

	def bind(self, name: str, key: Any, mode: str = "pulse") -> None:
		if mode not in ("pulse", "hold"):
			mode = "pulse"
		self.states[name] = PulseState(key=key, mode=mode)
		self.filtered[name] = 0.0

	def _ensure_released(self, st: PulseState) -> None:
		if st.pressed or st.held:
			try:
				self.kb.release(st.key)
			except Exception:
				pass
		st.pressed = False
		st.held = False
		st.release_due = 0.0

	def _press(self, st: PulseState) -> None:
		if not st.pressed:
			try:
				self.kb.press(st.key)
			except Exception:
				pass
			st.pressed = True

	def _release(self, st: PulseState) -> None:
		if st.pressed:
			try:
				self.kb.release(st.key)
			except Exception:
				pass
			st.pressed = False

	def update(self, name: str, raw_value: float, now: float) -> None:
		if name not in self.states:
			return
		st = self.states[name]

		# EMA smoothing to reduce jitter
		prev = self.filtered.get(name, 0.0)
		val = self.ema_alpha * raw_value + (1.0 - self.ema_alpha) * prev
		self.filtered[name] = val

		mag = abs(val)

		# Release opposite direction if needed (handled by caller by not updating it)

		if mag <= self.deadzone:
			# fully released below deadzone
			if st.held:
				self._ensure_released(st)
			elif st.pressed and now >= st.release_due:
				self._release(st)
			return

		if st.mode == "hold":
			# Always continuous when above deadzone
			if not st.held:
				self._press(st)
				st.held = True
			return
		else:
			# pulse mode with high-magnitude hold
			# Hold when strong input to keep motion smooth in-game
			if mag >= self.hold_threshold:
				if not st.held:
					# switch from pulse to hold
					self._press(st)
					st.held = True
				return

			# In pulsing range: ensure we're not in hold mode
			if st.held:
				self._release(st)
				st.held = False

			# Compute pulse frequency from magnitude
			# Map [deadzone, hold_threshold] -> [min_hz, max_hz]
			span = max(1e-6, self.hold_threshold - self.deadzone)
			unit = clamp((mag - self.deadzone) / span, 0.0, 1.0)
			freq = self.min_hz + unit * (self.max_hz - self.min_hz)
			interval = 1.0 / max(1e-6, freq)

			# Start a new pulse if interval elapsed
			if now - st.last_pulse_time >= interval:
				self._press(st)
				st.release_due = now + self.press_ms
				st.last_pulse_time = now

			# End pulse if its on-time elapsed
			if st.pressed and now >= st.release_due:
				self._release(st)


def main(device: Optional[str] = None, invert_yaw: bool = True) -> None:
	print("SpaceMouse → Keyboard (interpolated) using pyspacemouse")
	print("Press Ctrl+C to exit.")

	# Open SpaceMouse via library
	if device is not None:
		dev = sm_open(set_nonblocking_loop=True, device=device)
	else:
		# let pyspacemouse auto-pick the first supported device
		dev = sm_open(set_nonblocking_loop=True)
	if dev is None:
		print("No SpaceMouse device opened.")
		return

	kb = keyboard.Controller()
	# Movement controller (translation + rotation/pitch)
	ik = InterpolatedKeyController(
		kb,
		press_ms=MOVE_PRESS_MS,
		min_hz=MOVE_MIN_HZ,
		max_hz=MOVE_MAX_HZ,
		deadzone=MOVE_DEADZONE,
		hold_threshold=MOVE_HOLD_THRESHOLD,
		ema_alpha=MOVE_EMA_ALPHA,
	)
	# Zoom controller with separate sensitivity
	zoom_ik = InterpolatedKeyController(
		kb,
		press_ms=ZOOM_PRESS_MS,
		min_hz=ZOOM_MIN_HZ,
		max_hz=ZOOM_MAX_HZ,
		deadzone=ZOOM_DEADZONE,
		hold_threshold=ZOOM_HOLD_THRESHOLD,
		ema_alpha=ZOOM_EMA_ALPHA,
	)

	# Bind actions to keys
	# translation and zoom: pulse mode
	ik.bind("move_left", 'a', mode="pulse")
	ik.bind("move_right", 'd', mode="pulse")
	ik.bind("move_forward", 'w', mode="pulse")
	ik.bind("move_backward", 's', mode="pulse")
	zoom_ik.bind("zoom_in", keyboard.Key.page_up, mode="pulse")
	zoom_ik.bind("zoom_out", keyboard.Key.page_down, mode="pulse")
	# rotation (twist) and pitch: continuous hold for smooth camera
	ik.bind("rotate_left", keyboard.Key.delete, mode="hold")
	ik.bind("rotate_right", keyboard.Key.end, mode="hold")
	ik.bind("pitch_up", keyboard.Key.up, mode="hold")
	ik.bind("pitch_down", keyboard.Key.down, mode="hold")

	last_dir = {
		"x": 0,
		"y": 0,
		"z": 0,
		"yaw": 0,
	}

	try:
		while True:
			state = sm_read()
			now = time.time()
			if not state:
				time.sleep(0.005)
				continue

			# Read raw axes
			x = state.x
			y = state.y
			z = state.z
			yaw = state.yaw
			pitch = state.pitch

			# Apply global inversion flags
			if INVERT_X:
				x = -x
			if INVERT_Y:
				y = -y
			if INVERT_Z:
				z = -z
			# Keep CLI arg for yaw inversion but OR with config flag for convenience
			if INVERT_YAW:
				yaw = -yaw

			# Optional swap of Y and Z roles (move vs zoom)
			if SWAP_Y_Z:
				y, z = z, y

			# X axis -> A/D
			if x >= 0:
				ik.update("move_right", x, now)
				# ensure opposite key is idle
				ik.update("move_left", 0.0, now)
			else:
				ik.update("move_left", -x, now)
				ik.update("move_right", 0.0, now)

			# Y axis -> W/S
			if y <= 0:
				ik.update("move_forward", -y, now)
				ik.update("move_backward", 0.0, now)
			else:
				ik.update("move_backward", y, now)
				ik.update("move_forward", 0.0, now)

			# Z -> PageUp/PageDown
			if z >= 0:
				zoom_ik.update("zoom_in", z, now)
				zoom_ik.update("zoom_out", 0.0, now)
			else:
				zoom_ik.update("zoom_out", -z, now)
				zoom_ik.update("zoom_in", 0.0, now)

			# Yaw (twist) -> rotate left/right (continuous)
			if yaw >= 0:
				ik.update("rotate_right", yaw, now)
				ik.update("rotate_left", 0.0, now)
			else:
				ik.update("rotate_left", -yaw, now)
				ik.update("rotate_right", 0.0, now)

			# Pitch (continuous)
			if pitch >= 0:
				ik.update("pitch_up", abs(pitch), now)
				ik.update("pitch_down", 0.0, now)
			else:
				ik.update("pitch_down", abs(pitch), now)
				ik.update("pitch_up", 0.0, now)

			time.sleep(0.005)

	except KeyboardInterrupt:
		pass
	finally:
		# Ensure all keys released
		for st in list(ik.states.values()) + list(zoom_ik.states.values()):
			try:
				if st.pressed or st.held:
					kb.release(st.key)
			except Exception:
				pass
		try:
			sm_close()
		except Exception:
			pass


if __name__ == "__main__":
	main()
